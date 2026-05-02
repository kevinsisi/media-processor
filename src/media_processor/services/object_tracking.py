"""v0.16 — YOLOv8-based object tracking for the auto-reframe stage.

Per asset we sample at ``TRACKING_SAMPLE_FPS`` (5 Hz default), run a
single forward pass of YOLOv8n (nano variant, ~6 MB, ~6 ms per frame
on an RTX 2070), pick the dominant subject class across the clip, and
store the per-frame bounding box of that subject in
``Asset.tracking_json``.

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
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Detection cadence + confidence floor. 5 Hz at 30 s ≈ 150 detections;
# Kalman smoothing in services.auto_reframe interpolates to the render
# fps. Confidence floor 0.30 keeps weak detections out (especially on
# the first / last frames where the subject is partially in frame).
TRACKING_SAMPLE_FPS: float = float(os.environ.get("TRACKING_SAMPLE_FPS", "5"))
TRACKING_MIN_CONFIDENCE: float = float(
    os.environ.get("TRACKING_MIN_CONFIDENCE", "0.30")
)
# Default model — yolov8n.pt is the smallest official YOLOv8 weight
# (~6 MB), fits in <500 MB VRAM, and runs at 100+ fps on a 2070. Larger
# variants (s/m/l/x) trade speed for accuracy; override via env if you
# care more about precision than throughput.
TRACKING_MODEL: str = os.environ.get("TRACKING_MODEL", "yolov8n.pt")
# Where ultralytics caches downloaded weights inside the worker. Bind-
# mounted via the existing /app/media volume so a re-pull after a
# container restart is unnecessary.
TRACKING_MODEL_DIR: Path = Path(
    os.environ.get("TRACKING_MODEL_DIR", "/app/media/tracking_models")
)

# COCO class names we treat as "the subject" of a video. Order matters
# for tiebreaks: when two classes are detected with similar frequency
# the earlier entry wins. Tuned for short-form video content.
SUBJECT_CLASS_PRIORITY: tuple[str, ...] = (
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
class TrackingResult:
    subject_class: str
    confidence: float  # mean confidence across kept frames
    src_w: int
    src_h: int
    fps: float
    frames: tuple[Detection, ...] = field(default_factory=tuple)
    sampled_frames: int = 0


def _is_fake() -> bool:
    return os.environ.get("TRACKING_FAKE", "0") == "1"


def _fake_result(src_w: int = 1920, src_h: int = 1080, duration_ms: int = 5_000) -> TrackingResult:
    """Deterministic stub — a centered 'car' bbox sweeping left→right."""
    n = max(1, int(duration_ms * TRACKING_SAMPLE_FPS / 1000))
    bbox_w, bbox_h = src_w // 4, src_h // 2
    frames = []
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
    return TrackingResult(
        subject_class="car",
        confidence=0.85,
        src_w=src_w,
        src_h=src_h,
        fps=TRACKING_SAMPLE_FPS,
        frames=tuple(frames),
        sampled_frames=n,
    )


# Module-level cache so a worker process pays the model load cost
# exactly once across many tracking jobs.
_MODEL_CACHE: dict[str, Any] = {}


def _resolve_model_path(model_id: str) -> Path:
    """Return a local path to the YOLO weights, downloading on miss."""
    TRACKING_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    target = TRACKING_MODEL_DIR / model_id
    if target.is_file() and target.stat().st_size > 0:
        return target
    # ultralytics' YOLO() will auto-download to its cache (current dir
    # by default). Pull straight into our cache dir so the next worker
    # boot finds it.
    try:
        from ultralytics import YOLO  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — install-time guard
        raise TrackingUnavailableError(
            f"ultralytics not installed: {exc}"
        ) from exc
    cwd = Path.cwd()
    try:
        os.chdir(TRACKING_MODEL_DIR)
        YOLO(model_id)  # triggers download → cwd / model_id
    finally:
        os.chdir(cwd)
    if not target.is_file():
        raise TrackingUnavailableError(
            f"YOLO model {model_id} did not land at {target}"
        )
    return target


def _load_model(model_id: str) -> Any:
    cached = _MODEL_CACHE.get(model_id)
    if cached is not None:
        return cached
    try:
        from ultralytics import YOLO  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — install-time guard
        raise TrackingUnavailableError(
            f"ultralytics not installed: {exc}"
        ) from exc
    path = _resolve_model_path(model_id)
    model = YOLO(str(path))
    _MODEL_CACHE[model_id] = model
    logger.info("YOLO model loaded: %s", path)
    return model


def detect(
    media_path: Path, duration_ms: int, *, model_id: str = TRACKING_MODEL
) -> TrackingResult:
    """Run YOLO over ``media_path`` at ``TRACKING_SAMPLE_FPS`` and return
    the dominant subject's per-frame bbox track.

    Empty result (``frames=()``) is a valid outcome — many b-roll clips
    have no recognised subjects. Auto-reframe in that case falls back
    to a static center crop so the cut still renders.
    """
    if _is_fake():
        return _fake_result(duration_ms=duration_ms or 5_000)

    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — install-time guard
        raise TrackingUnavailableError(
            f"opencv missing: {exc}"
        ) from exc

    model = _load_model(model_id)

    cap = cv2.VideoCapture(str(media_path))
    if not cap.isOpened():
        raise TrackingError(f"OpenCV could not open {media_path}")
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    interval_ms = int(1000 / TRACKING_SAMPLE_FPS)
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
            # ultralytics returns a list of Results, one per image. We
            # passed a single frame so results[0] is what we want.
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
            # Pick the highest-confidence detection on this frame whose
            # class is in our subject whitelist. This avoids tracking a
            # background object (e.g. a "tv" in a Lambo interior shot).
            best: tuple[float, int, str] | None = None  # (conf, idx, name)
            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i].item())
                conf = float(boxes.conf[i].item())
                cls_name = names.get(cls_id, str(cls_id))
                if cls_name not in SUBJECT_CLASS_PRIORITY:
                    continue
                if best is None or conf > best[0]:
                    best = (conf, i, cls_name)
            if best is None:
                ts += interval_ms
                continue
            conf, idx, cls_name = best
            xyxy = boxes.xyxy[idx].tolist()  # [x1, y1, x2, y2]
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
        # carry an empty ``frames`` array.
        return TrackingResult(
            subject_class="",
            confidence=0.0,
            src_w=src_w,
            src_h=src_h,
            fps=TRACKING_SAMPLE_FPS,
            frames=(),
            sampled_frames=sampled,
        )

    # Pick the dominant class by total appearances; tie-break by the
    # ``SUBJECT_CLASS_PRIORITY`` order so person beats car when their
    # counts are equal.
    counts: Counter[str] = Counter(d.cls_name for d in detections)
    sorted_priority = {name: i for i, name in enumerate(SUBJECT_CLASS_PRIORITY)}
    dominant = sorted(
        counts.items(),
        key=lambda kv: (-kv[1], sorted_priority.get(kv[0], 999)),
    )[0][0]

    kept = [d for d in detections if d.cls_name == dominant]
    mean_conf = sum(d.confidence for d in kept) / max(1, len(kept))

    return TrackingResult(
        subject_class=dominant,
        confidence=mean_conf,
        src_w=src_w,
        src_h=src_h,
        fps=TRACKING_SAMPLE_FPS,
        frames=tuple(kept),
        sampled_frames=sampled,
    )


def serialise(result: TrackingResult) -> dict[str, Any]:
    """JSON-friendly dict suitable for ``Asset.tracking_json``."""
    return {
        "subject_class": result.subject_class,
        "confidence": round(result.confidence, 3),
        "src_w": result.src_w,
        "src_h": result.src_h,
        "fps": result.fps,
        "sampled_frames": result.sampled_frames,
        "frames": [
            {
                "t_ms": d.t_ms,
                "x": d.x,
                "y": d.y,
                "w": d.w,
                "h": d.h,
                "conf": round(d.confidence, 3),
            }
            for d in result.frames
        ],
    }


__all__ = [
    "SUBJECT_CLASS_PRIORITY",
    "TRACKING_MIN_CONFIDENCE",
    "TRACKING_MODEL",
    "TRACKING_SAMPLE_FPS",
    "Detection",
    "TrackingError",
    "TrackingResult",
    "TrackingUnavailableError",
    "detect",
    "serialise",
]
