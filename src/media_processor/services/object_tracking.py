"""v0.16 — YOLOv8-based object tracking for the auto-reframe stage.

Per asset we sample at ``TRACKING_SAMPLE_FPS`` (5 Hz default), run a
single forward pass of YOLOv8n (nano variant, ~6 MB, ~6 ms per frame
on an RTX 2070).

v0.17 widened the output: instead of returning only the dominant
subject's per-frame bbox, we now group every detection into per-class
"tracks" so the user can pick a non-dominant object on the analysis
page (e.g. follow the dog instead of the bigger person). The legacy
``frames`` / ``subject_class`` fields stay populated with the largest
track so older data + the auto-reframe default behaviour keep working.

The renderer's auto-reframe stage reads the same JSON and uses Kalman-
smoothed centers to drive a dynamic crop, keeping the subject centered
in the 9:16 / 4:5 / 1:1 output regardless of where it sits in the
source frame.

Pure subprocess-free: ultralytics handles its own torch + CUDA. Lazy
import so this module is cheap on the api side. ``TRACKING_FAKE=1``
test seam returns a deterministic stub so CI / non-GPU dev boxes can
drive the rest of the pipeline.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)


# Detection cadence + confidence floor. 5 Hz at 30 s ≈ 150 detections;
# Kalman smoothing in services.auto_reframe interpolates to the render
# fps. Confidence floor 0.30 keeps weak detections out (especially on
# the first / last frames where the subject is partially in frame).
TRACKING_SAMPLE_FPS: float = float(os.environ.get("TRACKING_SAMPLE_FPS", "5"))
TRACKING_MIN_CONFIDENCE: float = float(os.environ.get("TRACKING_MIN_CONFIDENCE", "0.30"))
# v0.22.2 — drop tracks with fewer than this many sampled detections
# from any user-facing surface (the /tracking endpoint, the
# detected-classes aggregator, the AssetTrackingTarget bbox overlay).
# At 5 Hz sampling, 5 frames ≈ 1 second of subject presence; anything
# shorter is almost always YOLO noise (a single mis-classified frame
# during fast motion / occlusion) and clutters the picker without
# being useful as a tracking target. Filter is applied at READ time
# so the raw tracking_json blob keeps the full data and we can
# lower the threshold later without re-running analysis.
MIN_TRACK_FRAMES: int = 5


def is_track_significant(track: dict[str, Any]) -> bool:
    """v0.22.2 — return True when ``track["frames"]`` is long enough
    to surface to the operator. The single source-of-truth predicate
    used by the tracking router, the detected-classes aggregator,
    and any other read-time filter."""
    if not isinstance(track, dict):
        return False
    frames = track.get("frames")
    return isinstance(frames, list) and len(frames) >= MIN_TRACK_FRAMES


# Default model — yolov8n.pt is the smallest official YOLOv8 weight
# (~6 MB), fits in <500 MB VRAM, and runs at 100+ fps on a 2070. Larger
# variants (s/m/l/x) trade speed for accuracy; override via env if you
# care more about precision than throughput.
TRACKING_MODEL: str = os.environ.get("TRACKING_MODEL", "yolov8n.pt")
# Where ultralytics caches downloaded weights inside the worker. Bind-
# mounted via the existing /app/media volume so a re-pull after a
# container restart is unnecessary.
TRACKING_MODEL_DIR: Path = Path(os.environ.get("TRACKING_MODEL_DIR", "/app/media/tracking_models"))

# COCO class names we treat as "the subject" of a video. Order matters
# for tiebreaks: when two classes are detected with similar frequency
# the earlier entry wins. v0.17 expanded from the short whitelist to
# the full COCO-80 vocabulary so the user can pick from anything YOLO
# saw (the analysis page exposes every detected class as a chip; the
# default dominant-track selection still prefers the historical
# "subject" classes via SUBJECT_CLASS_PRIORITY_HEAD below).
SUBJECT_CLASS_PRIORITY_HEAD: tuple[str, ...] = (
    "person",
    "car",
    "truck",
    "bus",
    "motorcycle",
    "bicycle",
    "dog",
    "cat",
    "horse",
    "skateboard",
)
COCO80_CLASSES: tuple[str, ...] = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)
# Back-compat alias — kept so nothing imports SUBJECT_CLASS_PRIORITY.
SUBJECT_CLASS_PRIORITY: tuple[str, ...] = SUBJECT_CLASS_PRIORITY_HEAD


class TrackingError(RuntimeError):
    """Generic detection failure (read frame, etc.)."""


class TrackingUnavailableError(TrackingError):
    """ultralytics / OpenCV / model file are not usable on this host."""


@dataclass(frozen=True)
class Detection:
    """One YOLO box on one sampled frame."""

    t_ms: int
    cls_name: str
    confidence: float
    x: int  # bbox top-left
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Track:
    """v0.17 — one tracked object across the asset.

    ``object_index`` is the stable id we expose to the user so they can
    pick "follow this dog" via the API. ``area_score`` is the mean
    bbox area / (src_w * src_h) — used for sorting tracks so the
    largest subject lands at the top of the picker UI.
    """

    object_index: int
    cls_name: str
    confidence: float
    area_score: float
    frames: tuple[Detection, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TrackingResult:
    subject_class: str
    confidence: float  # mean confidence across kept frames
    src_w: int
    src_h: int
    fps: float
    frames: tuple[Detection, ...] = field(default_factory=tuple)
    sampled_frames: int = 0
    # v0.17 — per-class tracks. The first entry is always the dominant
    # one (matches ``frames``/``subject_class``); the rest are sorted
    # by area_score descending.
    tracks: tuple[Track, ...] = field(default_factory=tuple)


def _is_fake() -> bool:
    return os.environ.get("TRACKING_FAKE", "0") == "1"


def _fake_result(src_w: int = 1920, src_h: int = 1080, duration_ms: int = 5_000) -> TrackingResult:
    """Deterministic stub — a centered 'car' bbox sweeping left→right."""
    n = max(1, int(duration_ms * TRACKING_SAMPLE_FPS / 1000))
    bbox_w, bbox_h = src_w // 4, src_h // 2
    frames: list[Detection] = []
    for i in range(n):
        progress = i / max(1, n - 1)
        cx = int(src_w * 0.25 + (src_w * 0.5) * progress)
        cy = src_h // 2
        frames.append(
            Detection(
                t_ms=int(i * 1000 / TRACKING_SAMPLE_FPS),
                cls_name="car",
                confidence=0.85,
                x=max(0, cx - bbox_w // 2),
                y=max(0, cy - bbox_h // 2),
                w=bbox_w,
                h=bbox_h,
            )
        )
    track = Track(
        object_index=0,
        cls_name="car",
        confidence=0.85,
        area_score=(bbox_w * bbox_h) / (src_w * src_h),
        frames=tuple(frames),
    )
    return TrackingResult(
        subject_class="car",
        confidence=0.85,
        src_w=src_w,
        src_h=src_h,
        fps=TRACKING_SAMPLE_FPS,
        frames=tuple(frames),
        sampled_frames=n,
        tracks=(track,),
    )


# Module-level cache so a worker process pays the model load cost
# exactly once across many tracking jobs.
_MODEL_CACHE: dict[str, Any] = {}


def _load_yolo_class() -> Any:
    try:
        module = cast(Any, import_module("ultralytics"))
    except ImportError as exc:  # pragma: no cover — install-time guard
        raise TrackingUnavailableError(f"ultralytics not installed: {exc}") from exc
    try:
        return module.YOLO
    except AttributeError as exc:  # pragma: no cover — dependency contract guard
        raise TrackingUnavailableError("ultralytics.YOLO is unavailable") from exc


def _resolve_model_path(model_id: str) -> Path:
    """Return a local path to the YOLO weights, downloading on miss."""
    TRACKING_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    target = TRACKING_MODEL_DIR / model_id
    if target.is_file() and target.stat().st_size > 0:
        return target
    # ultralytics' YOLO() will auto-download to its cache (current dir
    # by default). Pull straight into our cache dir so the next worker
    # boot finds it.
    yolo_class = _load_yolo_class()
    cwd = Path.cwd()
    try:
        os.chdir(TRACKING_MODEL_DIR)
        yolo_class(model_id)  # triggers download → cwd / model_id
    finally:
        os.chdir(cwd)
    if not target.is_file():
        raise TrackingUnavailableError(f"YOLO model {model_id} did not land at {target}")
    return target


def _load_model(model_id: str) -> Any:
    cached = _MODEL_CACHE.get(model_id)
    if cached is not None:
        return cached
    yolo_class = _load_yolo_class()
    path = _resolve_model_path(model_id)
    model = yolo_class(str(path))
    _MODEL_CACHE[model_id] = model
    logger.info("YOLO model loaded: %s", path)
    return model


def detect(media_path: Path, duration_ms: int, *, model_id: str = TRACKING_MODEL) -> TrackingResult:
    """Run YOLO over ``media_path`` at ``TRACKING_SAMPLE_FPS`` and return
    the dominant subject's per-frame bbox track.

    Empty result (``frames=()``) is a valid outcome — many b-roll clips
    have no recognised subjects. Auto-reframe in that case falls back
    to a static center crop so the cut still renders.
    """
    if _is_fake():
        return _fake_result(duration_ms=duration_ms or 5_000)

    try:
        import cv2
    except ImportError as exc:  # pragma: no cover — install-time guard
        raise TrackingUnavailableError(f"opencv missing: {exc}") from exc

    model = _load_model(model_id)

    cap = cv2.VideoCapture(str(media_path))
    if not cap.isOpened():
        raise TrackingError(f"OpenCV could not open {media_path}")
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    interval_ms = int(1000 / TRACKING_SAMPLE_FPS)
    # v0.17 — keep ALL detections regardless of class so the user can
    # pick a non-dominant object on the analysis page. We still bias
    # the dominant-track selection toward SUBJECT_CLASS_PRIORITY_HEAD
    # below so the historical default behaviour is preserved.
    detections: list[Detection] = []
    sampled = 0

    try:
        ts = 0
        while ts < duration_ms:
            cap.set(cv2.CAP_PROP_POS_MSEC, ts)
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            sampled += 1
            results = model.predict(
                frame,
                verbose=False,
                conf=TRACKING_MIN_CONFIDENCE,
            )
            if not results:
                ts += interval_ms
                continue
            r0 = results[0]
            names = r0.names  # {cls_id: cls_name}
            boxes = r0.boxes
            if boxes is None or len(boxes) == 0:
                ts += interval_ms
                continue
            # Per class, keep only the highest-confidence box on this
            # frame so a class that appears twice in one frame doesn't
            # split into two competing per-class tracks downstream.
            best_per_class: dict[str, tuple[float, int]] = {}
            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i].item())
                conf = float(boxes.conf[i].item())
                cls_name = names.get(cls_id, str(cls_id))
                prior = best_per_class.get(cls_name)
                if prior is None or conf > prior[0]:
                    best_per_class[cls_name] = (conf, i)
            for cls_name, (conf, idx) in best_per_class.items():
                xyxy = boxes.xyxy[idx].tolist()
                x1, y1, x2, y2 = (int(v) for v in xyxy)
                detections.append(
                    Detection(
                        t_ms=ts,
                        cls_name=cls_name,
                        confidence=conf,
                        x=max(0, x1),
                        y=max(0, y1),
                        w=max(1, x2 - x1),
                        h=max(1, y2 - y1),
                    )
                )
            ts += interval_ms
    finally:
        cap.release()

    if not detections:
        # No subject seen at all. Still return a non-None result so the
        # analysis pipeline can mark the step "done"; tracking_json will
        # carry an empty ``frames`` / ``tracks`` array.
        return TrackingResult(
            subject_class="",
            confidence=0.0,
            src_w=src_w,
            src_h=src_h,
            fps=TRACKING_SAMPLE_FPS,
            frames=(),
            sampled_frames=sampled,
            tracks=(),
        )

    # Group detections per class → one Track per class. ``area_score``
    # is the mean (bbox area / source area) across kept frames; we
    # sort tracks by it descending so the largest object lands first
    # (matches "the obvious subject" in most clips).
    by_class: dict[str, list[Detection]] = {}
    for d in detections:
        by_class.setdefault(d.cls_name, []).append(d)
    src_area = max(1, src_w * src_h)
    tracks_unsorted: list[Track] = []
    for cls_name, dets in by_class.items():
        mean_conf = sum(d.confidence for d in dets) / max(1, len(dets))
        mean_area = sum(d.w * d.h for d in dets) / max(1, len(dets))
        tracks_unsorted.append(
            Track(
                object_index=0,  # reassigned after sort
                cls_name=cls_name,
                confidence=mean_conf,
                area_score=mean_area / src_area,
                frames=tuple(dets),
            )
        )
    # Sort: priority-head classes first (preserves the M9.1 default
    # selection), then by area_score desc, then class name. Reassign
    # object_index 0..N-1 so callers can use it as a stable picker key.
    head_order = {name: i for i, name in enumerate(SUBJECT_CLASS_PRIORITY_HEAD)}
    tracks_sorted = sorted(
        tracks_unsorted,
        key=lambda t: (
            head_order.get(t.cls_name, 999),
            -t.area_score,
            t.cls_name,
        ),
    )
    tracks: list[Track] = []
    for i, t in enumerate(tracks_sorted):
        tracks.append(
            Track(
                object_index=i,
                cls_name=t.cls_name,
                confidence=t.confidence,
                area_score=t.area_score,
                frames=t.frames,
            )
        )
    dominant = tracks[0]

    return TrackingResult(
        subject_class=dominant.cls_name,
        confidence=dominant.confidence,
        src_w=src_w,
        src_h=src_h,
        fps=TRACKING_SAMPLE_FPS,
        frames=dominant.frames,
        sampled_frames=sampled,
        tracks=tuple(tracks),
    )


def _serialise_frames(frames: tuple[Detection, ...]) -> list[dict[str, Any]]:
    return [
        {
            "t_ms": d.t_ms,
            "x": d.x,
            "y": d.y,
            "w": d.w,
            "h": d.h,
            "conf": round(d.confidence, 3),
        }
        for d in frames
    ]


def aggregate_detected_classes(
    tracking_blobs: list[dict[str, Any] | None],
) -> list[dict[str, Any]]:
    """v0.21 — roll up every asset's ``tracking_json`` into a per-class
    summary suitable for the subject-class picker.

    Returns one row per detected class, sorted by ``total_frames``
    descending (most-frequent first). ``asset_count`` counts the
    distinct assets that contained at least one detection of the class.

    Reads the v0.17 ``tracks`` array. Falls back to the legacy
    top-level ``frames`` (and ``subject_class``) when ``tracks`` is
    missing — keeps pre-v0.17 stored blobs visible in the picker so
    operators don't have to re-run YOLO just to use the filter.

    ``None`` entries (assets that haven't been tracked yet) and blobs
    without recognisable shape are silently skipped — the caller
    decides how to surface "no detected classes" to the UI.
    """
    counts: dict[str, dict[str, int]] = {}
    for blob in tracking_blobs:
        if not isinstance(blob, dict):
            continue
        tracks = blob.get("tracks")
        seen_in_asset: set[str] = set()
        if isinstance(tracks, list) and tracks:
            for t in tracks:
                if not isinstance(t, dict):
                    continue
                cls = t.get("cls_name")
                if not isinstance(cls, str) or not cls:
                    continue
                frames = t.get("frames")
                nframes = len(frames) if isinstance(frames, list) else 0
                # v0.22.2 — drop noise tracks (single-frame YOLO
                # mis-classifications, brief occlusion artefacts)
                # so the operator's class picker only shows
                # subjects that were actually visible long enough
                # to be useful as a tracking target.
                if nframes < MIN_TRACK_FRAMES:
                    continue
                slot = counts.setdefault(cls, {"total_frames": 0, "asset_count": 0})
                slot["total_frames"] += nframes
                seen_in_asset.add(cls)
        else:
            cls = blob.get("subject_class")
            frames = blob.get("frames")
            if (
                isinstance(cls, str)
                and cls
                and isinstance(frames, list)
                and len(frames) >= MIN_TRACK_FRAMES
            ):
                slot = counts.setdefault(cls, {"total_frames": 0, "asset_count": 0})
                slot["total_frames"] += len(frames)
                seen_in_asset.add(cls)
        for cls in seen_in_asset:
            counts[cls]["asset_count"] += 1
    rows: list[dict[str, Any]] = [
        {
            "cls_name": cls,
            "total_frames": agg["total_frames"],
            "asset_count": agg["asset_count"],
        }
        for cls, agg in counts.items()
    ]
    rows.sort(key=lambda r: (-int(r["total_frames"]), r["cls_name"]))
    return rows


def serialise(result: TrackingResult) -> dict[str, Any]:
    """JSON-friendly dict suitable for ``Asset.tracking_json``.

    v0.17: emits the new ``tracks`` array alongside the legacy
    ``subject_class`` / ``frames`` fields so older readers
    (auto_reframe pre-v0.17) keep working.
    """
    return {
        "subject_class": result.subject_class,
        "confidence": round(result.confidence, 3),
        "src_w": result.src_w,
        "src_h": result.src_h,
        "fps": result.fps,
        "sampled_frames": result.sampled_frames,
        "frames": _serialise_frames(result.frames),
        "tracks": [
            {
                "object_index": t.object_index,
                "cls_name": t.cls_name,
                "confidence": round(t.confidence, 3),
                "area_score": round(t.area_score, 4),
                "frames": _serialise_frames(t.frames),
            }
            for t in result.tracks
        ],
    }


# ---------- v0.17 — custom ROI tracking ----------


def _fake_custom_roi_result(
    *,
    src_w: int,
    src_h: int,
    duration_ms: int,
    init_x: int,
    init_y: int,
    init_w: int,
    init_h: int,
) -> dict[str, Any]:
    """Deterministic stub for the FAKE path — emits the supplied ROI
    as a static bbox at the sample fps. Sufficient for CI / non-OpenCV
    dev boxes to exercise the persistence + render path."""
    n = max(1, int(duration_ms * TRACKING_SAMPLE_FPS / 1000))
    interval_ms = int(1000 / TRACKING_SAMPLE_FPS)
    frames = [
        {
            "t_ms": i * interval_ms,
            "x": init_x,
            "y": init_y,
            "w": init_w,
            "h": init_h,
            "conf": 1.0,
        }
        for i in range(n)
    ]
    return {
        "src_w": src_w,
        "src_h": src_h,
        "fps": TRACKING_SAMPLE_FPS,
        "init_t_ms": 0,
        "init": {"x": init_x, "y": init_y, "w": init_w, "h": init_h},
        "frames": frames,
        "sampled_frames": n,
    }


def track_custom_roi(
    media_path: Path,
    *,
    init_x: int,
    init_y: int,
    init_w: int,
    init_h: int,
    init_t_ms: int = 0,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    """Run an OpenCV CSRT tracker from ``init_t_ms`` onwards.

    Returns a JSON-friendly dict suitable for ``Asset.custom_roi_json``:
    ``{src_w, src_h, fps, init_t_ms, init: {x,y,w,h}, frames: [...], sampled_frames}``.

    The tracker samples at ``TRACKING_SAMPLE_FPS`` to match the YOLO
    cadence so auto_reframe's Kalman filter sees the same per-second
    measurement density. If CSRT loses the subject mid-clip we fall
    back to the last known bbox for the remainder rather than emitting
    a gap (auto_reframe interpolates straight through anyway).
    """
    if _is_fake():
        return _fake_custom_roi_result(
            src_w=1920,
            src_h=1080,
            duration_ms=duration_ms or 5_000,
            init_x=init_x,
            init_y=init_y,
            init_w=init_w,
            init_h=init_h,
        )

    try:
        import cv2
    except ImportError as exc:  # pragma: no cover
        raise TrackingUnavailableError(f"opencv missing: {exc}") from exc

    cap = cv2.VideoCapture(str(media_path))
    if not cap.isOpened():
        raise TrackingError(f"OpenCV could not open {media_path}")
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration_ms = duration_ms or int(
        cap.get(cv2.CAP_PROP_FRAME_COUNT) / max(1.0, cap.get(cv2.CAP_PROP_FPS) or 1.0) * 1000
    )

    init_x = max(0, min(src_w - 1, init_x))
    init_y = max(0, min(src_h - 1, init_y))
    init_w = max(2, min(src_w - init_x, init_w))
    init_h = max(2, min(src_h - init_y, init_h))

    interval_ms = int(1000 / TRACKING_SAMPLE_FPS)
    frames: list[dict[str, Any]] = []
    sampled = 0

    try:
        # OpenCV 4.5+ exposes legacy.TrackerCSRT_create on the legacy
        # module; older builds expose TrackerCSRT_create at the top level.
        creator = getattr(getattr(cv2, "legacy", cv2), "TrackerCSRT_create", None)
        if creator is None:
            creator = getattr(cv2, "TrackerCSRT_create", None)
        if creator is None:
            raise TrackingUnavailableError(
                "OpenCV build has no CSRT tracker (need opencv-contrib-python)"
            )

        cap.set(cv2.CAP_PROP_POS_MSEC, init_t_ms)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise TrackingError(f"could not seek to init_t_ms={init_t_ms} in {media_path}")
        tracker = creator()
        tracker.init(frame, (init_x, init_y, init_w, init_h))
        last_x, last_y, last_w, last_h = init_x, init_y, init_w, init_h
        frames.append(
            {
                "t_ms": int(init_t_ms),
                "x": last_x,
                "y": last_y,
                "w": last_w,
                "h": last_h,
                "conf": 1.0,
            }
        )
        sampled += 1

        ts = init_t_ms + interval_ms
        while ts < duration_ms:
            cap.set(cv2.CAP_PROP_POS_MSEC, ts)
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            sampled += 1
            ok2, bbox = tracker.update(frame)
            if ok2:
                bx, by, bw, bh = bbox
                last_x = max(0, int(bx))
                last_y = max(0, int(by))
                last_w = max(1, int(bw))
                last_h = max(1, int(bh))
                conf = 1.0
            else:
                conf = 0.0
            frames.append(
                {
                    "t_ms": int(ts),
                    "x": last_x,
                    "y": last_y,
                    "w": last_w,
                    "h": last_h,
                    "conf": conf,
                }
            )
            ts += interval_ms
    finally:
        cap.release()

    return {
        "src_w": src_w,
        "src_h": src_h,
        "fps": TRACKING_SAMPLE_FPS,
        "init_t_ms": int(init_t_ms),
        "init": {"x": init_x, "y": init_y, "w": init_w, "h": init_h},
        "frames": frames,
        "sampled_frames": sampled,
    }


__all__ = [
    "COCO80_CLASSES",
    "MIN_TRACK_FRAMES",
    "SUBJECT_CLASS_PRIORITY",
    "SUBJECT_CLASS_PRIORITY_HEAD",
    "TRACKING_MIN_CONFIDENCE",
    "TRACKING_MODEL",
    "TRACKING_SAMPLE_FPS",
    "Detection",
    "Track",
    "TrackingError",
    "TrackingResult",
    "TrackingUnavailableError",
    "aggregate_detected_classes",
    "detect",
    "is_track_significant",
    "serialise",
    "track_custom_roi",
]
