# M4 — Design notes

## 1. Why a separate worker container

Whisper and OpenCV are heavy. Pulling them into the existing `api` image would:

1. Inflate the api image from a few hundred MB to ~5 GB (CUDA + cuDNN + Whisper weights download on first run).
2. Tie request-response latency to whatever GPU job is in flight (FastAPI's BackgroundTasks share the event loop with HTTP serving).
3. Force the api container to depend on `nvidia-container-toolkit` even on environments where analysis is offloaded.

A dedicated `worker` container with the same source mount and a separate Dockerfile keeps the api lean, lets us run multiple worker replicas behind one Redis queue if we ever need it, and lets analysis workloads be paused independently of the read/write API.

Pyproject already has `rq>=2.0.0` and `redis>=5.2.0` declared (in anticipation of M4) and `redis:7-alpine` is already running in compose — we are filling in the missing piece, not adding a new platform.

## 2. Why faster-whisper, not openai-whisper

CTranslate2-based `faster-whisper` runs ~4× faster and uses ~2× less VRAM than `openai-whisper` for the same model size at the same accuracy. On RTX 2070 8 GB:

| model       | openai-whisper VRAM | faster-whisper int8_float16 VRAM | wall-time (10-min clip, RTX 2070) |
| ----------- | ------------------- | -------------------------------- | --------------------------------- |
| medium      | ~5 GB               | ~1.5 GB                          | ~50 s                             |
| large-v3    | ~10 GB (won't fit)  | ~5 GB                            | ~3 min                            |

Picking `medium / int8_float16` as default leaves headroom for OpenCV running concurrently, leaves the door open for `large-v3` via env override, and stays well under the 8 GB limit even with the model held resident across jobs.

## 3. Traditional Chinese output

Whisper internally trains on a mixed Chinese corpus and tends to emit Simplified by default for `language="zh"`. Two layers of bias:

1. `initial_prompt="以下是繁體中文影片逐字稿。"` nudges the decoder toward Traditional during recognition.
2. Post-conversion via `opencc.OpenCC("s2twp.json")` (Simplified → Traditional, Taiwan-region phrasing) catches anything the prompt misses. `opencc` is a small C++-backed library; the wheel is ~2 MB.

Both layers run inside the worker; the segments stored to DB are already zh-Hant.

## 4. Persistence shape: JSON segments vs normalised rows

The transcript is naturally a list of `(start, end, text)`. Two options:

- **Option A — `transcript_segments` table** with `idx, start_ms, end_ms, text`. Pro: queryable by time range. Con: every read is N rows; every edit is "delete+insert"; the typical use is "render the whole thing", which reads N rows for nothing.
- **Option B — `segments_json` column on `asset_transcripts`**. Pro: single round-trip read; edit is a single row update; matches how the UI renders. Con: not directly queryable by time.

We pick **B**. M4 has no time-range query path. If M5 needs one, FTS or a derived index can be added without breaking the storage shape.

## 5. Asset status state machine

Before M4: `pending → ready → … → archived` (existing, lightly enforced).
After M4 the `assets.status` accepted set adds `analyzing`, `analyzed`, `analysis_failed`. The state machine is:

```
pending  ──(upload complete + enqueue)──▶  analyzing
analyzing  ──(all 4 steps done or skipped)──▶  analyzed
analyzing  ──(any step throws past retries)──▶  analysis_failed
analyzed   ──(POST /analyze with force=true)──▶  analyzing
analysis_failed ──(POST /analyze)──▶  analyzing
```

Per-step granularity lives in `analysis_steps_json` (`{stt, scene, motion, coverage}` each `pending|running|done|failed:{reason}`). The top-level `analyzed` only requires that no step is in `running` or `pending`; a `failed` step does not block the asset from reaching `analyzed` — the operator sees which steps failed and may re-run individual ones.

This keeps partial value: even if Vision fails, the transcript and motion are still surfaced.

## 6. Job pipeline ordering

Order: `stt → scene → motion → coverage`.

- STT first because:
  - Coverage strictly needs the transcript.
  - It's the longest GPU job; running it first means failures surface fastest in the UI.
- Scene tagging next: Vision API calls are I/O-bound (key-pool rotation handles transient 429), GPU is idle, so we can sample frames + send requests in parallel within the step.
- Motion next: pure CPU OpenCV. Could in principle interleave with Vision; we keep them sequential in M4 for predictability and add interleaving as a follow-up if total job time becomes a problem.
- Coverage last: needs both the transcript and the project script body; if no script is set, it skips with `failed: missing-script`.

Each step is wrapped in its own `try/except`. A failure writes `failed:{reason}` into `analysis_steps_json` for that step and continues to the next step. The job exits successfully so RQ doesn't retry the whole pipeline.

## 7. Manual re-analyze and edit interaction

The transcript can be edited by the operator. We do NOT want a "re-analyze all" to silently overwrite their edits.

Rule: `POST /assets/{id}/analyze`:

- Without `force=true`: STT step is skipped if `asset_transcripts.edited=true`. Other steps run for whichever step is `pending|failed`.
- With `force=true`: every requested step (or all four if `steps` omitted) runs. STT replaces the transcript and resets `edited=false`. Coverage is recomputed. Scene/motion AssetTag rows for the same `(asset_id, source_model)` set are deleted before the step runs, then refilled.

Also: editing the project script (`PUT /projects/{id}/script`) invalidates `script_coverage` rows for that project's assets so the next analyze call recomputes them.

## 8. Optical-flow parameter choices

Farnebäck dense flow defaults that work well for handheld phone footage:

- `pyr_scale=0.5, levels=3, winsize=15, iterations=3, poly_n=5, poly_sigma=1.2`
- Pre-process: ffmpeg downscales to `320:-2 / 5 fps` so a 10-min HD clip becomes ~3000 small frames.
- Window: aggregate flow vectors over 1-second sliding windows. A window's dominant direction is taken from the median angle weighted by magnitude.
- Classification thresholds (tuneable in `services/camera_motion.py`):
  - `static`: median magnitude < 0.5 px/frame
  - `pan`: |Δx| / |Δy| > 2.5 and median magnitude > 1.5
  - `tilt`: |Δy| / |Δx| > 2.5 and median magnitude > 1.5
  - `zoom`: divergence of vector field > 0.4 and median magnitude > 1.0
  - `handheld`: angular variance > 1.5 rad and magnitude variance > 2.0
- Adjacent windows of the same class merge into a single segment (`AssetTag.time_ranges_ms = [[start, end]]`).

## 9. Gemini Vision prompt and aggregation

Per frame, we send the JPEG bytes inline plus this prompt:

```
你會看到一張影片擷取的畫面。請從以下標籤集中挑選 1–4 個最貼切的場景描述，
其餘忽略。只回傳 JSON：

{ "tags": [{"name": "<tag>", "confidence": 0..1}, ...] }

允許的 tag：
indoor, outdoor, studio, closeup, medium_shot, wide,
dynamic, static, bright, dim, mixed_light
```

Aggregation across N sampled frames:
- A tag enters the asset's `AssetTag` rows if it appears in ≥ 30 % of frames OR has confidence ≥ 0.8 on at least one frame.
- Stored confidence is the mean confidence across the frames where it appeared.
- `time_ranges_ms` is left null on scene tags — they describe the whole asset.

## 10. Gemini semantic-coverage prompt

Sent once per asset:

```
你是影片剪輯助手。下面是「腳本」與「逐字稿片段」。請判斷每個逐字稿片段
是否與腳本任一段落語意接近（不需逐字相同；若主旨、訴求、講述順序大致相符
即視為「照稿」）。

腳本：
{{script_body}}

逐字稿片段（idx, [start_ms - end_ms] text）：
{{numbered_segments}}

請輸出嚴格 JSON：
{
  "matches": [
    {
      "transcript_idx": <int>,
      "classification": "scripted" | "improvised",
      "confidence": <float 0..1>,
      "matched_script_excerpt": <string, 對應到的腳本節錄；improvised 留空字串>
    }
  ]
}
```

The model's `responseMimeType=application/json` is set so we get clean JSON. Server validates the schema, drops any segment idx not present in the input, and clamps confidence to [0, 1].

Coverage = `Σ duration(scripted_segments) / Σ duration(all_segments)`.
Count-based coverage = `# scripted / # total` for the UI's secondary number.

## 11. WHISPER_FAKE for CI and non-GPU dev boxes

When `WHISPER_FAKE=1`, `whisper_stt.transcribe(...)` returns a deterministic canned zh-Hant transcript regardless of input audio: 5 segments × ~3 s each, total ~15 s, fixed text. This lets:

- CI exercise the analysis pipeline end-to-end without GPU.
- A non-GPU dev box drive the UI flow (the fake path is the default for `pytest`).
- Integration tests assert pipeline orchestration without depending on transcription accuracy.

The fake path is plumbed at the service boundary — the worker entrypoint, router, and DB shape are identical regardless.

## 12. UI polling cadence

The project-analysis page is one of the few places where the user sits on the page while async work happens. Cadence:

- Initial load: 1 immediate fetch.
- While any asset has `status='analyzing'`: poll every 3 s.
- Once all assets are `analyzed | analysis_failed`: poll every 10 s for 1 minute (in case the operator just edited a script and is waiting for re-coverage), then stop polling.
- A manual "重新分析" press resets the counter and resumes 3-s cadence until all assets settle again.

The hook returns both the asset list and a `pollIntervalMs` so the page can show a subtle "更新中" indicator without flicker.

## 13. Failure surfaces

Each pipeline step records its failure mode as `failed:{reason}` where `{reason}` is one of:

- `gpu-unavailable` — CUDA initialisation failed; STT only.
- `quota-exhausted` — all Gemini keys returned 429.
- `model-error:{short_msg}` — non-retryable model-side error.
- `disk-error:{short_msg}` — chunk file missing, scratch dir unwritable.
- `timeout` — step exceeded 30 min hard limit.
- `missing-script` — coverage step found no project script.

The UI shows a localised summary chip per failure class (e.g. `配額耗盡 → 稍後再試`). Operators with developer access can hit `GET /assets/{id}` to read the raw reason string.

## 14. Migration and rollback

`0003_m4_analysis.py` is a forward-only set of `CREATE TABLE` + a column-add on `assets`; downgrade drops them in reverse. The CHECK constraint on `assets.status` is widened, not redefined, so a downgrade restores the M3 set without orphaning rows that are currently `pending` (the only legacy state in pre-M4 data).
