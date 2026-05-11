"""v0.30.0 — AI Smart Camera planner.

Adds an optional, opt-in stage between plan generation and render that
asks Gemini Vision to mark the visually-salient region(s) inside each
``CutPlanSegment``'s ``[asset_start_ms, asset_end_ms)`` window, then
derives a high-level camera directive — ``zoom_in`` / ``zoom_out`` /
``pan`` / ``None`` — that the renderer turns into an ffmpeg crop
expression.

Design notes:

* The whole feature is gated behind ``Project.smart_camera_enabled``.
  When that's ``False`` (default) we never come in here at all and the
  planner / renderer behave exactly like 0.29.0.
* One Gemini Vision call per cut, not per asset. The cut's chosen span
  may be a tiny excerpt of a long source clip — sampling against the
  asset's full duration would waste tokens (and can mis-locate the
  subject if the asset has multiple unrelated bits inside it).
* Up to 4 frames per cut, evenly spaced inside the span, capped per
  cut. Empirically the 3-rule classifier (zoom_in / zoom_out / pan)
  doesn't need finer sampling than that and it keeps the per-cut cost
  bounded.
* Failure semantics are partial-success: a Gemini quota error or a
  malformed JSON parse on cut ``i`` returns ``None`` for that cut, so
  the renderer keeps the static aspect crop on that segment and the
  rest of the cuts can still get their directive. The orchestrator
  treats the stage itself as ``done`` even when individual cuts
  failed (mirrors the BGM stage's partial-success contract).
* The directive's ``from_rect`` / ``to_rect`` are normalised
  ``(x, y, w, h)`` 0..1 so the renderer can map them through any
  source resolution + target aspect without re-querying.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import shutil
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from media_processor.services.edit_planner import (
    DYNAMIC_MOTIONS,
    CutPlan,
    CutPlanSegment,
)

logger = logging.getLogger(__name__)


SMART_CAMERA_SCHEMA_VERSION = "smart-camera.v1"

# Per-cut frame sampling cap. 4 keyframes is enough to spot a
# zoom_in / zoom_out / pan candidate without burning tokens — most
# IG cuts are 3–6 s so any tighter sampling just packs near-duplicates.
MAX_FRAMES_PER_CUT: int = 4

# Per-Gemini-call wall-clock budget. Vision normally responds in under
# 5 s; 30 s is a generous ceiling that still bounds the orchestrator
# stage even when a key throttles or the network drops.
PER_CUT_TIMEOUT_S: float = 30.0

# Directive-derivation thresholds. ``mean_area`` is the average of
# each focus_region's ``w_norm * h_norm`` across the (up-to-4) frames
# Gemini saw. The two extremes go to zoom_in / zoom_out; the middle
# band stays static.
ZOOM_IN_AREA_MAX: float = 0.25
ZOOM_OUT_AREA_MIN: float = 0.60

# Two regions count as separate clusters when their bbox IoU is below
# this threshold. Below that and the planner emits ``pan`` instead of
# zooming — the operator's feedback specifically called out
# "the camera is static while the subject walks across the frame" as a
# situation where a pan reads better than a Ken-Burns zoom.
CLUSTER_DISJOINT_IOU: float = 0.10

# Zoom curve's start/end offsets — we crop into the span by 50 ms on
# each side so the camera move starts after any inbound xfade has
# settled (mirrors v0.14.1's ``TRANSITION_OVERLAP_MS`` logic).
TRANSITION_OVERLAP_MS: int = 500
EDGE_TRIM_S: float = 0.05

# Final camera-frame size, expressed as a fraction of the source
# frame. ``ZOOM_IN`` ends with the camera at 1.4× the source (i.e.
# its window is 1/1.4 ≈ 0.714 of the source); ``ZOOM_OUT`` starts at
# 1.3× and lands at 1.0×. These are the values v0.30.0 ships with;
# operator feedback may tune them.
ZOOM_IN_END_SCALE: float = 1.55
ZOOM_OUT_START_SCALE: float = 1.45
PAN_SCALE: float = 1.35  # pan keeps a constant zoom factor; the move is the directive
FALLBACK_ZOOM_IN_END_SCALE: float = 1.18
FALLBACK_ZOOM_OUT_START_SCALE: float = 1.16
FALLBACK_PAN_SCALE: float = 1.28

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class SmartCameraError(RuntimeError):
    """Base class for the planner's failure modes."""


class SmartCameraQuotaError(SmartCameraError):
    """Every supplied API key exhausted (429 / 5xx) on a single cut."""


class SmartCameraInvalidError(SmartCameraError):
    """Gemini returned a 200 but the JSON was unparseable."""


@dataclass(frozen=True)
class FocusRegion:
    """One bounding box reported by Gemini Vision for a single frame.

    Coordinates are normalised 0..1; ``t_norm`` is the frame's
    timestamp normalised against the cut's span (0.0 = first sampled
    frame, 1.0 = last). ``salience`` is Gemini's confidence the
    box is the focal point; we keep the field but don't currently
    weight by it (the rule-set is purely geometric).
    """

    t_norm: float
    x_norm: float
    y_norm: float
    w_norm: float
    h_norm: float
    salience: float = 1.0

    @property
    def area_norm(self) -> float:
        return max(0.0, self.w_norm * self.h_norm)

    @property
    def cx_norm(self) -> float:
        return self.x_norm + self.w_norm / 2.0

    @property
    def cy_norm(self) -> float:
        return self.y_norm + self.h_norm / 2.0


@dataclass(frozen=True)
class Directive:
    """One camera-move directive derived for a single ``CutPlanSegment``.

    ``kind`` is one of ``zoom_in`` / ``zoom_out`` / ``pan``. ``from_rect``
    and ``to_rect`` are normalised crop windows 0..1: the renderer
    interpolates between them across the cut's duration to drive ffmpeg's
    ``crop=W:H:x:y`` expression. ``ease`` picks a linear or exp
    interpolation curve.
    """

    kind: str  # "zoom_in" | "zoom_out" | "pan"
    from_rect: tuple[float, float, float, float]  # x, y, w, h normalised
    to_rect: tuple[float, float, float, float]
    ease: str = "linear"  # "linear" | "exp"
    # For diagnostics — kept on the directive so the FE / log can
    # surface "we picked zoom_in because mean area was 0.18".
    notes: str = ""


# ---------- frame sampling ----------


def _sample_cut_frames(
    src: Path,
    *,
    asset_start_ms: int,
    asset_end_ms: int,
    out_dir: Path,
    n_frames: int = MAX_FRAMES_PER_CUT,
) -> list[Path]:
    """Sample up to ``n_frames`` evenly-spaced JPEGs from a cut span.

    Mirrors ``services.scene_tagging._sample_frames`` but seeks into
    the source first so we only decode the cut's window, not the
    whole asset. Returns the JPEG paths in time order; caller is
    responsible for rmtree'ing ``out_dir`` afterwards.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jpg"):
        old.unlink(missing_ok=True)

    span_s = max(0.001, (asset_end_ms - asset_start_ms) / 1000.0)
    target_count = max(1, min(n_frames, int(round(span_s)) + 1))
    fps = max(0.1, target_count / span_s)

    pattern = str(out_dir / "frame_%04d.jpg")
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{asset_start_ms / 1000.0:.3f}",
        "-i",
        str(src),
        "-t",
        f"{span_s:.3f}",
        "-vf",
        f"fps={fps:.4f}",
        "-frames:v",
        str(target_count),
        "-q:v",
        "5",
        pattern,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
    if proc.returncode != 0:
        raise SmartCameraError(
            f"smart-camera frame sampling failed (code={proc.returncode}): "
            f"{proc.stderr.decode(errors='replace')[:300]}"
        )
    frames = sorted(out_dir.glob("frame_*.jpg"))
    if not frames:
        raise SmartCameraError("smart-camera ffmpeg sampling produced no frames")
    return frames


# ---------- prompt + parse ----------


_VISION_PROMPT = (
    "你會看到一段影片中均勻取樣的數張畫面（依時間先後）。"
    "請對「每一張」畫面回傳該畫面中視覺上最重要的 1–3 個重點區域（人臉、動作主體、產品、文字等）。\n"
    "座標以 0..1 之間的正規化值表示（左上角 0,0；右下角 1,1）。每個 bbox 須為矩形 (x, y, w, h)，"
    "其中 (x, y) 為左上角，(w, h) 為寬高，皆在 [0, 1] 之內。\n"
    "請務必嚴格輸出 JSON：\n"
    '{ "frames": [\n'
    '   { "index": 0, "regions": [ {"x":0..1,"y":0..1,"w":0..1,"h":0..1,"salience":0..1}, ... ] },\n'
    "   ...\n"
    "] }\n"
    '若該畫面沒有可辨識的重點，仍須輸出空 regions："regions": []。'
    "不要回傳框以外的文字、不要回傳描述、不要 markdown fence。"
)


def _strip_fence(text: str) -> str:
    match = _FENCE_RE.match(text)
    if match:
        return match.group(1)
    return text


def _clamp01(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SmartCameraInvalidError(f"expected float in [0,1]; got {value!r}")
    return max(0.0, min(1.0, float(value)))


def _parse_focus_regions(
    payload: dict[str, Any],
    *,
    sampled_frame_count: int,
) -> list[FocusRegion]:
    """Parse Gemini's JSON response into a flat list of FocusRegion."""
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise SmartCameraInvalidError("Vision payload missing candidates")
    parts = candidates[0].get("content", {}).get("parts", [])
    if not isinstance(parts, list) or not parts:
        raise SmartCameraInvalidError("Vision candidate missing content.parts")
    text = parts[0].get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise SmartCameraInvalidError("Vision candidate text empty")
    cleaned = _strip_fence(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise SmartCameraInvalidError(
            f"Vision JSON parse failed: {exc}; text={text[:200]}"
        ) from exc
    if not isinstance(data, dict):
        raise SmartCameraInvalidError("Vision JSON must be an object")
    raw_frames = data.get("frames")
    if not isinstance(raw_frames, list):
        raise SmartCameraInvalidError("Vision JSON missing frames list")

    frame_count = max(1, sampled_frame_count)
    out: list[FocusRegion] = []
    for entry in raw_frames:
        if not isinstance(entry, dict):
            continue
        try:
            idx_raw = entry.get("index", 0)
            idx = int(idx_raw) if isinstance(idx_raw, int | float) else 0
        except (TypeError, ValueError):
            idx = 0
        idx = max(0, min(frame_count - 1, idx))
        # Spread the frames evenly inside [0, 1]; index 0 → 0.0,
        # last index → 1.0, single-frame cut clamps at 0.0.
        t_norm = idx / max(1, frame_count - 1) if frame_count > 1 else 0.0
        regions = entry.get("regions")
        if not isinstance(regions, list):
            continue
        for r in regions:
            if not isinstance(r, dict):
                continue
            try:
                x = _clamp01(r.get("x"))
                y = _clamp01(r.get("y"))
                w = _clamp01(r.get("w"))
                h = _clamp01(r.get("h"))
            except SmartCameraInvalidError:
                # Skip malformed boxes rather than failing the whole
                # cut — partial parses are still useful.
                continue
            if w <= 0.0 or h <= 0.0:
                continue
            salience_raw = r.get("salience", 1.0)
            try:
                salience = _clamp01(salience_raw)
            except SmartCameraInvalidError:
                salience = 1.0
            out.append(
                FocusRegion(
                    t_norm=t_norm,
                    x_norm=x,
                    y_norm=y,
                    w_norm=w,
                    h_norm=h,
                    salience=salience,
                )
            )
    return out


async def _call_vision(
    client: httpx.AsyncClient,
    *,
    api_keys: tuple[str, ...],
    model: str,
    base_url: str,
    frame_bytes_list: Sequence[bytes],
) -> list[FocusRegion]:
    """Send the (up to 4) JPEGs to Gemini Vision; rotate keys on 429 / 5xx."""
    parts: list[dict[str, Any]] = [{"text": _VISION_PROMPT}]
    for frame_bytes in frame_bytes_list:
        parts.append(
            {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(frame_bytes).decode("ascii"),
                }
            }
        )
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }
    last_status = 0
    last_invalid: SmartCameraInvalidError | None = None
    for key in api_keys:
        url = f"{base_url}/models/{model}:generateContent?key={key}"
        try:
            response = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            logger.warning("smart-camera transport error; rotating key: %s", exc)
            continue
        last_status = response.status_code
        if response.status_code == 429 or 500 <= response.status_code < 600:
            logger.warning("smart-camera Vision %d; rotating to next key", response.status_code)
            continue
        if response.status_code >= 400:
            raise SmartCameraError(
                f"smart-camera Vision call failed: status={response.status_code} "
                f"body={response.text[:200]}"
            )
        try:
            return _parse_focus_regions(
                response.json(),
                sampled_frame_count=len(frame_bytes_list),
            )
        except SmartCameraInvalidError as exc:
            last_invalid = exc
            logger.warning("smart-camera invalid JSON (%s); rotating key", exc)
            continue
    if last_invalid is not None:
        raise last_invalid
    raise SmartCameraQuotaError(
        f"smart-camera: all {len(api_keys)} keys exhausted; last_status={last_status}"
    )


# ---------- directive derivation ----------


def _bbox_iou(a: FocusRegion, b: FocusRegion) -> float:
    ax2 = a.x_norm + a.w_norm
    ay2 = a.y_norm + a.h_norm
    bx2 = b.x_norm + b.w_norm
    by2 = b.y_norm + b.h_norm
    ix1 = max(a.x_norm, b.x_norm)
    iy1 = max(a.y_norm, b.y_norm)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    union = a.area_norm + b.area_norm - inter
    if union <= 0.0:
        return 0.0
    return inter / union


def _cluster_regions(regions: Iterable[FocusRegion]) -> list[list[FocusRegion]]:
    """Greedy single-link cluster — two regions in the same cluster when
    their IoU >= ``CLUSTER_DISJOINT_IOU``. Fast enough at 4×3 = 12
    boxes per cut (worst case).
    """
    clusters: list[list[FocusRegion]] = []
    for region in regions:
        placed = False
        for cluster in clusters:
            if any(_bbox_iou(region, m) >= CLUSTER_DISJOINT_IOU for m in cluster):
                cluster.append(region)
                placed = True
                break
        if not placed:
            clusters.append([region])
    return clusters


def _cluster_bbox(cluster: Sequence[FocusRegion]) -> tuple[float, float, float, float]:
    """Return the encompassing (x, y, w, h) of every region in ``cluster``."""
    if not cluster:
        return (0.0, 0.0, 1.0, 1.0)
    x1 = min(r.x_norm for r in cluster)
    y1 = min(r.y_norm for r in cluster)
    x2 = max(r.x_norm + r.w_norm for r in cluster)
    y2 = max(r.y_norm + r.h_norm for r in cluster)
    return (x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1))


def _crop_window_around(
    cx: float,
    cy: float,
    *,
    scale: float,
) -> tuple[float, float, float, float]:
    """Build a centred crop window of size ``1/scale`` around (cx, cy).

    The window is clamped so it never spills off the source — an
    extreme centre (e.g. focus near the edge) just snaps the window
    inside the source bounds.
    """
    w = 1.0 / max(1.0, scale)
    h = 1.0 / max(1.0, scale)
    x = cx - w / 2.0
    y = cy - h / 2.0
    x = max(0.0, min(1.0 - w, x))
    y = max(0.0, min(1.0 - h, y))
    return (x, y, w, h)


def _derive_directive(
    regions: Sequence[FocusRegion],
    *,
    dominant_motion: str,
) -> Directive | None:
    """Apply the v0.30.0 rule-set to a focus_regions list.

    Returns ``None`` when no rule fires — the renderer treats that
    as "no camera move on this cut".
    """
    if not regions:
        return None

    clusters = _cluster_regions(regions)
    # Sort clusters by total area so the dominant subject wins ties
    # (e.g. when multiple small clusters all clear the disjoint
    # threshold, the largest one drives zoom_in).
    clusters.sort(key=lambda c: sum(r.area_norm for r in c), reverse=True)

    ease = "exp" if dominant_motion in DYNAMIC_MOTIONS else "linear"

    if len(clusters) >= 2:
        # PAN: from first cluster's bbox centre → last cluster's
        # bbox centre. We pick the temporally first / last cluster
        # (sort by minimum t_norm in the cluster) so the camera
        # follows the actual chronological motion, not the geometric
        # ordering.
        chrono = sorted(clusters, key=lambda c: min(r.t_norm for r in c))
        first = chrono[0]
        last = chrono[-1]
        if _bbox_iou_clusters(first, last) >= CLUSTER_DISJOINT_IOU:
            # Two clusters but they actually overlap — fall back to
            # a single-cluster decision.
            clusters = [first + last]
        else:
            from_bbox = _cluster_bbox(first)
            to_bbox = _cluster_bbox(last)
            from_cx = from_bbox[0] + from_bbox[2] / 2.0
            from_cy = from_bbox[1] + from_bbox[3] / 2.0
            to_cx = to_bbox[0] + to_bbox[2] / 2.0
            to_cy = to_bbox[1] + to_bbox[3] / 2.0
            return Directive(
                kind="pan",
                from_rect=_crop_window_around(from_cx, from_cy, scale=PAN_SCALE),
                to_rect=_crop_window_around(to_cx, to_cy, scale=PAN_SCALE),
                ease=ease,
                notes=(
                    f"pan: {len(clusters)} clusters; "
                    f"{from_cx:.2f},{from_cy:.2f} → {to_cx:.2f},{to_cy:.2f}"
                ),
            )

    # Single cluster — decide between zoom_in / zoom_out / no-op
    # based on mean area.
    cluster = clusters[0]
    mean_area = sum(r.area_norm for r in cluster) / max(1, len(cluster))
    bbox = _cluster_bbox(cluster)
    cx = bbox[0] + bbox[2] / 2.0
    cy = bbox[1] + bbox[3] / 2.0

    if mean_area < ZOOM_IN_AREA_MAX:
        return Directive(
            kind="zoom_in",
            from_rect=(0.0, 0.0, 1.0, 1.0),
            to_rect=_crop_window_around(cx, cy, scale=ZOOM_IN_END_SCALE),
            ease=ease,
            notes=f"zoom_in: mean_area={mean_area:.3f}",
        )
    if mean_area > ZOOM_OUT_AREA_MIN:
        return Directive(
            kind="zoom_out",
            from_rect=_crop_window_around(cx, cy, scale=ZOOM_OUT_START_SCALE),
            to_rect=(0.0, 0.0, 1.0, 1.0),
            ease=ease,
            notes=f"zoom_out: mean_area={mean_area:.3f}",
        )
    return None


def _fallback_directive_for_cut(
    cut: CutPlanSegment,
    *,
    reason: str,
) -> Directive:
    """Return a visible deterministic move when Vision produces no directive.

    The product contract for the toggle is now literal: enabling AI Smart
    Camera must affect the render. Vision-derived focus still wins, but an
    empty / failed / mid-band result no longer falls through to a static crop.
    """
    motion = getattr(cut, "dominant_motion", "static")
    ease = "exp" if motion in DYNAMIC_MOTIONS else "linear"
    order = int(getattr(cut, "order", 0))

    if motion == "tilt":
        top = _crop_window_around(0.5, 0.34 if order % 2 == 0 else 0.66, scale=FALLBACK_PAN_SCALE)
        bottom = _crop_window_around(0.5, 0.66 if order % 2 == 0 else 0.34, scale=FALLBACK_PAN_SCALE)
        return Directive(
            kind="pan",
            from_rect=top,
            to_rect=bottom,
            ease=ease,
            notes=f"fallback tilt pan: {reason}",
        )

    if motion in {"pan", "handheld"} or order % 3 == 1:
        left = _crop_window_around(0.36 if order % 2 == 0 else 0.64, 0.5, scale=FALLBACK_PAN_SCALE)
        right = _crop_window_around(0.64 if order % 2 == 0 else 0.36, 0.5, scale=FALLBACK_PAN_SCALE)
        return Directive(
            kind="pan",
            from_rect=left,
            to_rect=right,
            ease=ease,
            notes=f"fallback lateral pan: {reason}",
        )

    if order % 3 == 2:
        return Directive(
            kind="zoom_out",
            from_rect=_crop_window_around(0.5, 0.5, scale=FALLBACK_ZOOM_OUT_START_SCALE),
            to_rect=(0.0, 0.0, 1.0, 1.0),
            ease=ease,
            notes=f"fallback zoom_out: {reason}",
        )

    return Directive(
        kind="zoom_in",
        from_rect=(0.0, 0.0, 1.0, 1.0),
        to_rect=_crop_window_around(0.5, 0.5, scale=FALLBACK_ZOOM_IN_END_SCALE),
        ease=ease,
        notes=f"fallback zoom_in: {reason}",
    )


def _bbox_iou_clusters(a: Sequence[FocusRegion], b: Sequence[FocusRegion]) -> float:
    """IoU of the encompassing bboxes for two clusters."""
    ax, ay, aw, ah = _cluster_bbox(a)
    bx, by, bw, bh = _cluster_bbox(b)
    if aw <= 0.0 or ah <= 0.0 or bw <= 0.0 or bh <= 0.0:
        return 0.0
    ix1 = max(ax, bx)
    iy1 = max(ay, by)
    ix2 = min(ax + aw, bx + bw)
    iy2 = min(ay + ah, by + bh)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    if union <= 0.0:
        return 0.0
    return inter / union


# ---------- serialisation (for CutPlanSegment.smart_camera_json) ----------


def serialise_directive(
    directive: Directive | None,
    *,
    focus_regions: Sequence[FocusRegion] | None = None,
) -> dict[str, Any] | None:
    """Pack a Directive (+ the source focus_regions for diagnostics)
    into the JSON dict the planner stores on
    ``CutPlanSegment.smart_camera_json``.
    """
    if directive is None:
        return None
    payload: dict[str, Any] = {
        "schema_version": SMART_CAMERA_SCHEMA_VERSION,
        "kind": directive.kind,
        "from_rect": list(directive.from_rect),
        "to_rect": list(directive.to_rect),
        "ease": directive.ease,
        "notes": directive.notes,
    }
    if focus_regions is not None:
        payload["focus_regions"] = [
            {
                "t_norm": round(r.t_norm, 4),
                "x_norm": round(r.x_norm, 4),
                "y_norm": round(r.y_norm, 4),
                "w_norm": round(r.w_norm, 4),
                "h_norm": round(r.h_norm, 4),
                "salience": round(r.salience, 4),
            }
            for r in focus_regions
        ]
    return payload


def deserialise_directive(blob: dict[str, Any] | None) -> Directive | None:
    """Inverse of ``serialise_directive`` — used by the renderer to
    pick the camera move out of ``CutPlanSegment.smart_camera_json``
    without re-importing the planner's parsing logic."""
    if not isinstance(blob, dict):
        return None
    kind = blob.get("kind")
    if kind not in ("zoom_in", "zoom_out", "pan"):
        return None
    try:
        from_rect = tuple(float(v) for v in blob["from_rect"])
        to_rect = tuple(float(v) for v in blob["to_rect"])
    except (KeyError, TypeError, ValueError):
        return None
    if len(from_rect) != 4 or len(to_rect) != 4:
        return None
    from_rect4 = (from_rect[0], from_rect[1], from_rect[2], from_rect[3])
    to_rect4 = (to_rect[0], to_rect[1], to_rect[2], to_rect[3])
    ease = str(blob.get("ease", "linear"))
    if ease not in ("linear", "exp"):
        ease = "linear"
    return Directive(
        kind=str(kind),
        from_rect=from_rect4,
        to_rect=to_rect4,
        ease=ease,
        notes=str(blob.get("notes", "")),
    )


# ---------- top-level entry point ----------


@dataclass
class _SegmentPlan:
    """Internal pairing of (CutPlanSegment, derived directive)."""

    order: int
    directive: Directive | None
    focus_regions: list[FocusRegion] = field(default_factory=list)
    error: str | None = None


async def plan_smart_camera(
    plan: CutPlan,
    asset_paths: dict[int, Path],
    *,
    api_keys: tuple[str, ...],
    model: str,
    base_url: str,
    timeout_s: float = PER_CUT_TIMEOUT_S,
    scratch_dir: Path,
) -> dict[int, dict[str, Any]]:
    """Run smart-camera analysis over every cut in ``plan``.

    Returns a mapping ``{segment.order: smart_camera_json}`` containing
    the directive dicts for every cut where Gemini Vision returned
    usable focus_regions AND ``_derive_directive`` produced a non-None
    move. Cuts that failed the Vision call or fell through to ``None``
    are simply absent from the dict — the orchestrator should treat
    "missing key" as "no camera move on that cut" so a partial Gemini
    failure doesn't block the whole stage.

    Per cut, we sample up to ``MAX_FRAMES_PER_CUT`` JPEGs into a
    scratch sub-dir and rmtree it afterwards regardless of success
    so a long-running process doesn't accumulate temp frames.
    """
    if not api_keys:
        raise SmartCameraError("no API keys configured for smart-camera Vision")

    scratch_dir = Path(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    out: dict[int, dict[str, Any]] = {}

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for cut in plan.segments:
            src = asset_paths.get(cut.asset_id)
            if src is None or not Path(src).is_file():
                logger.warning(
                    "smart-camera: cut order=%d asset=%d source missing; skipping",
                    cut.order,
                    cut.asset_id,
                )
                continue
            cut_scratch = scratch_dir / f"cut_{cut.order:04d}"
            try:
                frame_paths = await asyncio.to_thread(
                    _sample_cut_frames,
                    Path(src),
                    asset_start_ms=cut.asset_start_ms,
                    asset_end_ms=cut.asset_end_ms,
                    out_dir=cut_scratch,
                )
                frame_bytes_list = [p.read_bytes() for p in frame_paths]
                regions = await _call_vision(
                    client,
                    api_keys=api_keys,
                    model=model,
                    base_url=base_url,
                    frame_bytes_list=frame_bytes_list,
                )
                directive = _derive_directive(
                    regions,
                    dominant_motion=getattr(cut, "dominant_motion", "static"),
                )
                if directive is None:
                    directive = _fallback_directive_for_cut(cut, reason="vision returned no move")
                blob = serialise_directive(directive, focus_regions=regions)
                if blob is not None:
                    out[cut.order] = blob
                logger.info(
                    "smart-camera: cut order=%d asset=%d → %s",
                    cut.order,
                    cut.asset_id,
                    directive.kind if directive is not None else "none",
                )
            except SmartCameraError as exc:
                logger.warning(
                    "smart-camera: cut order=%d asset=%d failed: %s",
                    cut.order,
                    cut.asset_id,
                    exc,
                )
                directive = _fallback_directive_for_cut(cut, reason=type(exc).__name__)
                blob = serialise_directive(directive)
                if blob is not None:
                    out[cut.order] = blob
            except Exception:  # noqa: BLE001 — never bring down the stage.
                logger.exception(
                    "smart-camera: cut order=%d asset=%d unexpected error",
                    cut.order,
                    cut.asset_id,
                )
                directive = _fallback_directive_for_cut(cut, reason="unexpected error")
                blob = serialise_directive(directive)
                if blob is not None:
                    out[cut.order] = blob
            finally:
                shutil.rmtree(cut_scratch, ignore_errors=True)
    return out


def build_fallback_directives(plan: CutPlan, *, reason: str) -> dict[int, dict[str, Any]]:
    """Build smart-camera directives without a Vision call.

    Used when the toggle is enabled but API keys are missing/exhausted before
    sampling starts. The render still gets visible camera motion instead of
    silently becoming a static crop.
    """
    out: dict[int, dict[str, Any]] = {}
    for cut in plan.segments:
        blob = serialise_directive(_fallback_directive_for_cut(cut, reason=reason))
        if blob is not None:
            out[cut.order] = blob
    return out


def apply_smart_camera_to_plan(
    plan: CutPlan,
    directives_by_order: dict[int, dict[str, Any]],
) -> CutPlan:
    """Return a new CutPlan with each segment's ``smart_camera_json``
    populated from ``directives_by_order``. Segments missing from
    the dict keep their existing ``smart_camera_json`` (which is
    ``None`` on a fresh plan).
    """
    new_segments: list[CutPlanSegment] = []
    for seg in plan.segments:
        if seg.order in directives_by_order:
            new_segments.append(
                CutPlanSegment(
                    order=seg.order,
                    asset_id=seg.asset_id,
                    asset_start_ms=seg.asset_start_ms,
                    asset_end_ms=seg.asset_end_ms,
                    source_kind=seg.source_kind,
                    reason=seg.reason,
                    transition_to_next=seg.transition_to_next,
                    dominant_emotion=seg.dominant_emotion,
                    dominant_motion=seg.dominant_motion,
                    has_face=seg.has_face,
                    smart_camera_json=directives_by_order[seg.order],
                )
            )
        else:
            new_segments.append(seg)
    return CutPlan(
        schema_version=plan.schema_version,
        target_duration_ms=plan.target_duration_ms,
        target_aspect_ratio=plan.target_aspect_ratio,
        profile_name=plan.profile_name,
        segments=tuple(new_segments),
        notes=plan.notes,
        used_fallback=plan.used_fallback,
        fallback_reason=plan.fallback_reason,
    )


__all__ = [
    "CLUSTER_DISJOINT_IOU",
    "EDGE_TRIM_S",
    "MAX_FRAMES_PER_CUT",
    "PAN_SCALE",
    "SMART_CAMERA_SCHEMA_VERSION",
    "TRANSITION_OVERLAP_MS",
    "ZOOM_IN_AREA_MAX",
    "ZOOM_IN_END_SCALE",
    "ZOOM_OUT_AREA_MIN",
    "ZOOM_OUT_START_SCALE",
    "Directive",
    "FocusRegion",
    "SmartCameraError",
    "SmartCameraInvalidError",
    "SmartCameraQuotaError",
    "_derive_directive",
    "apply_smart_camera_to_plan",
    "build_fallback_directives",
    "deserialise_directive",
    "plan_smart_camera",
    "serialise_directive",
]
