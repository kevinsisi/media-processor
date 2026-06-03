# Frame Analysis Prompt Skill

Use this skill before changing `services/frame_analysis_service.py` or the Vision prompt that describes sampled video frames for NarratoAI documentary mode.

## Contract

- Input is an ordered batch of JPEG frames sampled from one asset.
- Output must be strict JSON with `frame_observations` and `overall_activity_summary`.
- `frame_observations` length must match the input frame count.
- Each observation should describe visible subjects, scene, actions, and changes that help later narration script generation.
- Keep output in Traditional Chinese where possible.

## Guardrails

- Do not ask the model to choose final cuts in this prompt; that belongs to StoryScript generation.
- Preserve batch-level summaries because `analysis_to_markdown()` depends on them.
- Keep failures recoverable: invalid or unavailable Vision output must degrade to fallback observations instead of blocking every draft.
