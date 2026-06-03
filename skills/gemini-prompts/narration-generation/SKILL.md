# Narration Generation Prompt Skill

Use this skill before changing `services/narration_script_generator.py` or prompts for documentary/drama_explain StoryScript generation.

## Contract

- Output must be strict StoryScript JSON with `schema_version`, `title`, `summary`, and `items`.
- Every item must include `order`, `asset_id`, `source_start_ms`, `source_end_ms`, `picture`, `narration`, `audio_intent`, `beat_type`, and `reason`.
- Supported `audio_intent` values are `narration`, `original`, and `narration_with_original`.
- Documentary mode should treat frame-analysis observations as visual source material and default to `audio_intent=narration`.
- Drama explain mode should use transcript-derived beats and may use `audio_intent=original` only for important original-sound moments.

## Guardrails

- Do not emit markdown fences or explanatory prose around JSON.
- Keep source ranges within each asset duration; invalid ranges are rejected by StoryScript validation.
- Prefer Traditional Chinese narration. `opencc_converter.to_traditional()` is a fallback, not a replacement for prompt quality.
- If LLM output fails validation, the service must keep a deterministic fallback path.
