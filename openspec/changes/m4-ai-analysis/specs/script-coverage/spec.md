# script-coverage (NEW)

## Purpose

Compare the corrected transcript of an asset against the project's script and produce a coverage number plus per-segment `scripted | improvised` classifications. The comparison is semantic, not literal — operators write loose scripts and improvise around them, so character-level diffs would be uselessly noisy.

## Requirements

### REQ-1: Inputs

- The service requires:
  - The asset's transcript: `asset_transcripts.segments_json` (zh-Hant, with timestamps).
  - The project's script: `scripts.body` (zh-Hant plain text).
- If either is missing or empty, the service records `failed:missing-script` (for missing/empty script) or `failed:disk-error:no-transcript` (for missing transcript) and exits without writing a coverage row.

### REQ-2: Single-call Gemini comparison

- The service makes a single Gemini text-generation call (model from `GEMINI_VISION_MODEL` env reused for text — or a dedicated `GEMINI_TEXT_MODEL` if introduced later) with `responseMimeType=application/json`.
- The prompt provides the script body and a numbered list of transcript segments (`idx [start_ms - end_ms] text`) and asks the model to return `{matches: [{transcript_idx, classification: "scripted"|"improvised", confidence, matched_script_excerpt}]}`.
- Key-pool rotation reuses the LLM patcher's pattern; 429 / 5xx rotate keys.

### REQ-3: Validation

- Server-side validation drops any `transcript_idx` not present in the input list.
- `confidence` is clamped to `[0, 1]`.
- `classification` values other than `scripted` or `improvised` reject the response and the service either retries with the next key or, after exhaustion, records `failed:model-error:bad-classification`.

### REQ-4: Coverage computation

- `coverage_ratio_by_count = (# scripted) / (# total transcript segments)` rounded to 4 decimal places.
- `coverage_ratio_by_duration_ms = sum(end_ms - start_ms for scripted) / sum(end_ms - start_ms for all)` rounded to 4 decimal places.
- Both are computed on the server from the validated matches (not taken from the model output) so the model can't return inconsistent totals.

### REQ-5: Persistence

- One `script_coverage` row per asset (delete-then-insert if one already exists). Stores `(asset_id, script_id, model, scripted_segment_count, total_segment_count, coverage_ratio_by_count, coverage_ratio_by_duration_ms, match_details_json, computed_at)`.
- `match_details_json` holds the full validated `matches` array.

### REQ-6: Invalidation on script edit

- `PUT /projects/{id}/script` deletes all `script_coverage` rows whose `script_id` matches the project's script. Affected assets must be re-analyzed for coverage to reappear.
- `PUT /assets/{id}/transcript` (operator edit) does NOT auto-invalidate coverage — the operator may want to compare the edited transcript against the existing coverage as a sanity check. They re-trigger via `POST /assets/{id}/analyze` with `steps: ["coverage"]` when ready.

### REQ-7: Failure modes

- Quota exhaustion across keys → `failed:quota-exhausted`.
- Malformed model response after retries → `failed:model-error:invalid-json` or `failed:model-error:bad-classification`.
- Missing project script → `failed:missing-script` (the only "skip" reason — the step does NOT run).
- Missing transcript → `failed:disk-error:no-transcript`.
