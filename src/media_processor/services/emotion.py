"""Phase 8.1 — face emotion analysis via MediaPipe Face Landmarker.

For each asset we sample 1 frame per ``EMOTION_SAMPLE_INTERVAL_MS`` and run
the MediaPipe Face Landmarker with blendshapes enabled. The 52 blendshape
coefficients are reduced to one of {happy, surprised, serious, neutral}
per sampled frame, then adjacent same-class samples are merged into time
ranges, and the longest-aggregate class is reported as ``dominant``.

Why MediaPipe blendshapes rather than a CNN emotion model: blendshapes
ship inside a ~4 MB ``.task`` file so the worker image stays small, and
the heuristic mapping below stays explicit / auditable rather than a
black-box classifier. Tradeoff: blendshapes describe facial geometry not
psychology, so we map four broad classes the renderer can act on.

Module is gracefully degradable:
- Importing this file does NOT pull mediapipe (lazy ``import``).
- If mediapipe / opencv / model file are missing, ``classify_asset``
  raises ``EmotionUnavailableError`` so the orchestrator records
  ``failed:model-missing`` and the rest of the analysis continues.
- If no faces are detected at all (product shots, b-roll, etc.) the
  step returns an empty result rather than raising.
"""

from __future__ import annotations

import logging
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# Canonical emotion classes the rest of the pipeline assumes. Anything
# outside this set leaks into the planner / renderer and causes unsafe
# defaults, so changes here ripple — search ``EMOTION_TAGS`` first.
EMOTION_TAGS: tuple[str, ...] = ("happy", "surprised", "serious", "neutral")
EMOTION_DEFAULT: str = "neutral"

# Sampling cadence — 2 fps is enough to track expression shifts on phone
# footage without blowing the per-asset analysis time. Tunable via env.
EMOTION_SAMPLE_INTERVAL_MS: int = int(os.environ.get("EMOTION_SAMPLE_INTERVAL_MS", "500"))

# Blendshape thresholds — empirical, derived from MediaPipe demo runs on
# zh-Hant influencer footage. Each rule is independent so multiple rules
# can fire on one frame; ``_classify_blendshapes`` picks the strongest.
SMILE_THRESHOLD = 0.45  # mouthSmileLeft + mouthSmileRight averaged
JAW_OPEN_THRESHOLD = 0.40
BROW_RAISE_THRESHOLD = 0.35  # browInnerUp / browOuterUp averaged
BROW_DOWN_THRESHOLD = 0.30  # browDownLeft + browDownRight averaged
EYE_WIDE_THRESHOLD = 0.30

# MediaPipe Face Landmarker .task file — auto-downloaded on first use to
# the configured cache dir so the worker image build does not need to
# bake in a Google CDN dependency. Override ``EMOTION_MODEL_PATH`` to
# use a pre-downloaded file (e.g. an air-gapped deploy).
EMOTION_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)
EMOTION_MODEL_DIR = Path(os.environ.get("EMOTION_MODEL_DIR", "/app/media/emotion_models"))
EMOTION_MODEL_FILENAME = "face_landmarker.task"


class EmotionAnalysisError(RuntimeError):
    """Generic failure during emotion analysis (read frame, etc.)."""


class EmotionUnavailableError(EmotionAnalysisError):
    """mediapipe / opencv / model file are not usable on this host."""


@dataclass(frozen=True)
class EmotionRange:
    """One contiguous block of frames classified as the same emotion."""

    emotion: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class EmotionResult:
    """Aggregated emotion verdict for a whole asset."""

    dominant: str
    ranges: tuple[EmotionRange, ...]
    sampled_frames: int
    faces_seen: int


def _resolve_model_path() -> Path:
    """Return the path to the face_landmarker .task file, downloading on miss.

    ``EMOTION_MODEL_PATH`` overrides everything (used by tests / sandbox
    deploys to point at a pre-shipped file). Otherwise we cache under
    ``EMOTION_MODEL_DIR`` and fetch lazily — first call pays the ~4 MB
    download, every subsequent call is a stat. Network failures surface
    as ``EmotionUnavailableError`` so the worker records the right token.
    """
    override = os.environ.get("EMOTION_MODEL_PATH")
    if override:
        path = Path(override)
        if not path.is_file():
            raise EmotionUnavailableError(
                f"EMOTION_MODEL_PATH set to {override} but the file is missing"
            )
        return path
    EMOTION_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    target = EMOTION_MODEL_DIR / EMOTION_MODEL_FILENAME
    if target.is_file() and target.stat().st_size > 0:
        return target
    logger.info("emotion: downloading MediaPipe model to %s", target)
    try:
        with urllib.request.urlopen(EMOTION_MODEL_URL, timeout=60) as response:  # nosec - public CDN
            target.write_bytes(response.read())
    except Exception as exc:  # noqa: BLE001 — surface as unavailable.
        raise EmotionUnavailableError(
            f"failed to download MediaPipe face_landmarker.task: {exc}"
        ) from exc
    return target


def _classify_blendshapes(blendshapes: dict[str, float]) -> str:
    """Map a single frame's blendshape dict to one of EMOTION_TAGS.

    The mapping is intentionally simple so the user can audit it: a
    strong smile beats everything; jaw drop + raised brows reads as
    surprise; lowered brows read as serious; everything else is
    neutral. When two rules fire we use the order below (happy first
    because it tends to be the most actionable signal for the renderer).
    """
    smile = (blendshapes.get("mouthSmileLeft", 0.0) + blendshapes.get("mouthSmileRight", 0.0)) / 2
    jaw_open = blendshapes.get("jawOpen", 0.0)
    brow_up = (blendshapes.get("browInnerUp", 0.0) + blendshapes.get("browOuterUpLeft", 0.0)) / 2
    brow_down = (
        blendshapes.get("browDownLeft", 0.0) + blendshapes.get("browDownRight", 0.0)
    ) / 2
    eye_wide = (
        blendshapes.get("eyeWideLeft", 0.0) + blendshapes.get("eyeWideRight", 0.0)
    ) / 2

    if smile >= SMILE_THRESHOLD:
        return "happy"
    if jaw_open >= JAW_OPEN_THRESHOLD and (
        brow_up >= BROW_RAISE_THRESHOLD or eye_wide >= EYE_WIDE_THRESHOLD
    ):
        return "surprised"
    if brow_down >= BROW_DOWN_THRESHOLD:
        return "serious"
    return "neutral"


def _merge_adjacent(samples: list[tuple[int, str]]) -> list[EmotionRange]:
    """Compress (timestamp_ms, emotion) samples into contiguous ranges.

    Each sample represents a window of length EMOTION_SAMPLE_INTERVAL_MS
    centered on its timestamp; the merged range stretches from the first
    sample's start to the last sample's end (timestamp + interval).
    """
    if not samples:
        return []
    out: list[EmotionRange] = []
    interval = EMOTION_SAMPLE_INTERVAL_MS
    cur_start = samples[0][0]
    cur_emotion = samples[0][1]
    cur_last = samples[0][0]
    for ts, emo in samples[1:]:
        if emo == cur_emotion:
            cur_last = ts
            continue
        out.append(
            EmotionRange(emotion=cur_emotion, start_ms=cur_start, end_ms=cur_last + interval)
        )
        cur_start = ts
        cur_emotion = emo
        cur_last = ts
    out.append(EmotionRange(emotion=cur_emotion, start_ms=cur_start, end_ms=cur_last + interval))
    return out


def _pick_dominant(ranges: list[EmotionRange]) -> str:
    """Return the emotion with the largest summed duration across ranges.

    Ties broken by EMOTION_TAGS declaration order so the verdict stays
    deterministic across reruns.
    """
    if not ranges:
        return EMOTION_DEFAULT
    totals: dict[str, int] = dict.fromkeys(EMOTION_TAGS, 0)
    for r in ranges:
        totals[r.emotion] = totals.get(r.emotion, 0) + (r.end_ms - r.start_ms)
    best_dur = -1
    best_tag = EMOTION_DEFAULT
    for tag in EMOTION_TAGS:
        if totals.get(tag, 0) > best_dur:
            best_dur = totals[tag]
            best_tag = tag
    return best_tag


def _is_fake() -> bool:
    """Test seam: ``EMOTION_FAKE=1`` short-circuits the whole pipeline.

    Returns a fixed ``EmotionResult`` ('happy' middle range) so the rest
    of the orchestration tests can drive the analysis path without
    mediapipe being installed in CI.
    """
    return os.environ.get("EMOTION_FAKE", "0") == "1"


def classify_asset(media_path: Path, duration_ms: int) -> EmotionResult:
    """Run face landmarker over ``media_path`` and return EmotionResult.

    ``duration_ms`` is used to clamp the sampling loop — a shorter clip
    yields fewer samples without re-probing the file. Empty result
    (``ranges=()``) is a valid outcome for clips without faces.
    """
    if _is_fake():
        # Deterministic stub for orchestration tests.
        ranges = (
            EmotionRange(emotion="happy", start_ms=0, end_ms=min(duration_ms, 2000)),
            EmotionRange(
                emotion="neutral",
                start_ms=min(duration_ms, 2000),
                end_ms=duration_ms,
            ),
        ) if duration_ms > 0 else ()
        dominant = _pick_dominant(list(ranges)) if ranges else EMOTION_DEFAULT
        return EmotionResult(
            dominant=dominant,
            ranges=ranges,
            sampled_frames=len(ranges),
            faces_seen=len(ranges),
        )

    try:
        import cv2  # type: ignore[import-not-found]
        import mediapipe as mp  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - install-time guard
        raise EmotionUnavailableError(f"mediapipe / opencv missing: {exc}") from exc

    model_path = _resolve_model_path()

    base_options = mp.tasks.BaseOptions(model_asset_path=str(model_path))
    options = mp.tasks.vision.FaceLandmarkerOptions(
        base_options=base_options,
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        num_faces=1,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=False,
    )

    cap = cv2.VideoCapture(str(media_path))
    if not cap.isOpened():
        raise EmotionAnalysisError(f"OpenCV could not open {media_path}")

    samples: list[tuple[int, str]] = []
    sampled = 0
    faces_seen = 0
    try:
        with mp.tasks.vision.FaceLandmarker.create_from_options(options) as landmarker:
            ts = 0
            while ts < duration_ms:
                cap.set(cv2.CAP_PROP_POS_MSEC, ts)
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                sampled += 1
                # mediapipe expects RGB.
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = landmarker.detect(mp_image)
                blendshapes = result.face_blendshapes if result else None
                if not blendshapes:
                    ts += EMOTION_SAMPLE_INTERVAL_MS
                    continue
                faces_seen += 1
                bs_dict = {b.category_name: float(b.score) for b in blendshapes[0]}
                samples.append((ts, _classify_blendshapes(bs_dict)))
                ts += EMOTION_SAMPLE_INTERVAL_MS
    finally:
        cap.release()

    ranges = _merge_adjacent(samples)
    dominant = _pick_dominant(ranges)
    return EmotionResult(
        dominant=dominant,
        ranges=tuple(ranges),
        sampled_frames=sampled,
        faces_seen=faces_seen,
    )


__all__ = [
    "EMOTION_DEFAULT",
    "EMOTION_TAGS",
    "EmotionAnalysisError",
    "EmotionRange",
    "EmotionResult",
    "EmotionUnavailableError",
    "classify_asset",
]
