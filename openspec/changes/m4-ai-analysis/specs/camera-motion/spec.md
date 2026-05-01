# camera-motion (NEW)

## Purpose

Classify how the camera moves over time by computing dense optical flow on a downscaled copy of the video, summarising 1-second windows, and emitting `pan / tilt / zoom / static / handheld` segments as `AssetTag` rows with time ranges.

## Requirements

### REQ-1: Pre-downscale

- Before flow computation, the asset is downscaled via ffmpeg to `320:-2` width and 5 fps into `${MEDIA_STORAGE_DIR}/analysis/{asset_id}/motion.mp4`.
- The downscaled file is the input to OpenCV. The original asset file is never re-decoded for motion analysis.
- The downscaled file is removed at end of step (success or failure).

### REQ-2: Optical flow

- Dense flow is computed via `cv2.calcOpticalFlowFarneback` between consecutive frames with parameters declared as module constants in `services/camera_motion.py` (no inline magic numbers): `pyr_scale=0.5, levels=3, winsize=15, iterations=3, poly_n=5, poly_sigma=1.2`.
- Per-frame flow magnitude and angle are reduced to median + variance over the frame's pixels.

### REQ-3: Window aggregation and classification

- Frames are grouped into 1-second sliding windows (5 frames at 5 fps). Windows shorter than 0.8 s at the tail of the clip are merged into the previous window.
- A window's classification is one of `static | pan | tilt | zoom | handheld`, decided by the thresholds declared as module constants:
  - `static` if median magnitude < 0.5 px/frame
  - `pan` if `|median Δx| / |median Δy| > 2.5` AND median magnitude > 1.5
  - `tilt` if `|median Δy| / |median Δx| > 2.5` AND median magnitude > 1.5
  - `zoom` if flow-field divergence > 0.4 AND median magnitude > 1.0
  - `handheld` if angular variance > 1.5 rad AND magnitude variance > 2.0
  - else `handheld` (catch-all for noisy motion that fits no other class)
- Allowed `tag_name` values are exactly `static, pan, tilt, zoom, handheld`. Defined in a `MOTION_TAGS` constant.

### REQ-4: Segment merging and persistence

- Adjacent windows of the same class merge into one segment.
- Per merged segment, one `AssetTag(asset_id, tag_type='motion', tag_name=…, confidence=1.0, source_model='opencv-optical-flow', time_ranges_ms=[[start_ms, end_ms]])` row is inserted.
- `confidence` is set to `1.0` because OpenCV is deterministic; classification confidence is encoded structurally in the choice of class.
- With `force=true`, existing motion rows for the asset (`tag_type='motion' AND source_model='opencv-optical-flow'`) are deleted before insertion.

### REQ-5: CPU-only

- Motion analysis runs on CPU. It does not consume GPU and can run while a Vision API call is in flight in a sibling step (orchestration ordering aside — see `analysis-pipeline` REQ-2 for the canonical sequence).

### REQ-6: Failure modes

- ffmpeg downscale fails → `failed:disk-error:{message}`.
- OpenCV processing exception → `failed:model-error:{exception_class}`.
- Step exceeds 30-min budget → `failed:timeout` (handled by the orchestrator, not this service).
