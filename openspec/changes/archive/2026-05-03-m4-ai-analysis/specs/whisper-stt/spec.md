# whisper-stt (NEW)

## Purpose

Convert the audio track of an uploaded video into Traditional-Chinese SRT-style segments using `faster-whisper` on the dispatch host's GPU, while keeping the rest of the analysis pipeline workable on non-GPU developer boxes via a fake mode.

## Requirements

### REQ-1: Engine and model

- The transcription engine is `faster-whisper` (CTranslate2-based). `openai-whisper` is not used.
- Default model is `medium`, configurable via `WHISPER_MODEL` env (`medium` or `large-v3`). Default `compute_type` is `int8_float16`, configurable via `WHISPER_COMPUTE_TYPE`. Default device is `cuda`, configurable via `WHISPER_DEVICE`.
- The model is lazy-loaded on first call inside the worker process and cached for the worker's lifetime.

### REQ-2: Traditional-Chinese output

- Decoding always passes `language="zh"` and `initial_prompt="以下是繁體中文影片逐字稿。"`.
- Every segment's `text` is post-processed through OpenCC `s2twp.json` so any simplified-character drift becomes Traditional-Chinese (Taiwan-region phrasing).
- The persisted `transcript_text` is the joined segment texts separated by `\n`; the `segments_json` array contains `{idx, start_ms, end_ms, text}`.

### REQ-3: Persistence shape

- After successful transcription, the worker upserts an `asset_transcripts` row with `(asset_id, language='zh-Hant', model=f"faster-whisper-{WHISPER_MODEL}", transcript_text, segments_json, edited=False)`.
- Existing rows for the same asset are replaced (delete-then-insert OR explicit upsert; either is allowed).

### REQ-4: Fake mode for CI / non-GPU dev

- When `WHISPER_FAKE=1`, the service skips loading any model and returns a deterministic canned zh-Hant transcript: 5 segments × ~3 s each, total ~15 s of audio time, fixed text content. Persistence path is unchanged.
- The default value of `WHISPER_FAKE` for `pytest` and CI is `1`. Production `worker` container leaves it unset (so the real engine runs).

### REQ-5: GPU unavailability

- If CUDA initialisation fails (e.g. driver mismatch, no GPU exposed), the service raises a typed exception that the pipeline maps to `failed:gpu-unavailable` for the `stt` step. It does NOT silently fall back to CPU because CPU `medium` is too slow to be useful (>10× realtime).

### REQ-6: Audio extraction

- The service does not require a separate audio file; `faster-whisper` accepts the original media file and pulls audio via its built-in ffmpeg shell-out.
- If the asset's media file is missing on disk, the service raises and the pipeline records `failed:disk-error:{message}`.
