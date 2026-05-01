# scene-tagging (NEW)

## Purpose

Attach a small, generic, fixed-vocabulary set of scene descriptors to each asset by sampling frames and asking Gemini Vision to choose from the allowed enum. Stays industry-agnostic so the same tags work for travel, product, food, lifestyle, etc.

## Requirements

### REQ-1: Fixed tag vocabulary

- The allowed `tag_name` values for `tag_type='scene'` are exactly: `indoor`, `outdoor`, `studio`, `closeup`, `medium_shot`, `wide`, `dynamic`, `static`, `bright`, `dim`, `mixed_light`. Any tag returned by the model that is not in this enum is dropped.
- These values are defined in a `SCENE_TAGS` constant in `services/scene_tagging.py`. No magic strings inline.

### REQ-2: Frame sampling

- Frames are sampled at one frame every `SCENE_SAMPLE_INTERVAL_MS` milliseconds (default 5000). Sampling is implemented via ffmpeg `-vf fps=…` to a temp directory under `${MEDIA_STORAGE_DIR}/analysis/{asset_id}/frames/`.
- A maximum of 60 frames per asset is sampled regardless of duration; for longer assets the interval is increased to keep the count at or below the cap. (Cost cap on Vision API.)

### REQ-3: Vision call

- Each frame is POSTed to Gemini Vision (model from `GEMINI_VISION_MODEL` env, default `gemini-2.0-flash`) with a fixed prompt that instructs the model to return JSON of the form `{tags: [{name, confidence}, …]}` choosing only from the allowed enum.
- The Vision call uses the same key-pool rotation pattern as `services/llm_patcher.py` (`GeminiKeyPoolConfig`-style); 429 / 5xx rotates to the next key.
- `responseMimeType=application/json` is set so we get clean JSON.

### REQ-4: Aggregation

- A tag fires for the asset if it appears in ≥ 30 % of sampled frames OR has confidence ≥ 0.8 in at least one frame.
- The persisted confidence is the mean confidence across the frames in which the tag appeared.
- Per-tag `time_ranges_ms` is stored as `null` — scene tags describe the whole asset, not specific time ranges.

### REQ-5: Persistence

- After aggregation, the service inserts one `AssetTag(asset_id, tag_type='scene', tag_name=…, confidence=…, source_model='gemini-vision-2.0-flash')` row per fired tag.
- The unique constraint on `(asset_id, tag_type, tag_name, source_model)` ensures repeated runs without `force=true` no-op rather than duplicate.
- With `force=true`, all matching rows for the asset are deleted before insertion.

### REQ-6: Cleanup

- The frame scratch dir is removed after the step completes (success or failure). A failure does not leave thumbnails lingering.

### REQ-7: Failure modes

- Network / 429 quota exhaustion across all keys → `failed:quota-exhausted`.
- ffmpeg sampling fails → `failed:disk-error:{message}`.
- Model returns malformed JSON or no allowed tags after retries → `failed:model-error:no-valid-tags`.
