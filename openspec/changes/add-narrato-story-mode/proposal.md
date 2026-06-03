## Why

The current media-processor pipeline is strong at asset management, rendering, stabilization, subtitles, BGM, and review workflow, but its planning layer still behaves like a clip selector rather than a short-form story/script generator. NarratoAI demonstrates a better operator-facing path for social shorts: turn subtitles and sampled frames into a structured story script with hooks, narration, retained-original-audio beats, and timestamped edit intent before rendering.

## What Changes

- Add a NarratoAI-inspired story mode that can generate a short-form script from existing transcripts, uploaded subtitles, and optional sampled-frame visual analysis.
- Introduce a durable StoryScript contract with timestamped source ranges, picture descriptions, narration text, beat metadata, and an OST-like audio intent enum.
- Add a conversion path from StoryScript items to the existing `CutPlan` / `DraftSegment` model so current cut, concat, subtitle, BGM, watermark, Smart Camera, and export stages remain the rendering source of truth.
- Make GPU-heavy local analysis optional for story-mode generation; the core story path should work with external ASR / vision / text providers and CPU ffmpeg rendering.
- Defer TTS narration audio generation to a follow-up implementation phase, but design the StoryScript and timeline contracts so narration-aware duration adjustment can be added without replacing the renderer.
- Add user-facing controls and guidance for choosing Story/Narrato mode without disrupting existing standard, luxury-auto, and viral-short flows.

## Capabilities

### New Capabilities
- `story-script-mode`: Generate and persist Narrato-style short-form story scripts from transcripts/subtitles and optional visual analysis, then convert them into renderable draft plans.

### Modified Capabilities
- `workflow`: Add a story-mode generation path from analysis/edit entry points while preserving the existing one-click draft generation workflow.
- `novice-social-shorts-workflow`: Surface story-mode outputs and actions in beginner-friendly Traditional Chinese so operators can preview, edit, regenerate, and publish social shorts without seeing backend implementation terms.
- `analysis-next-step-guidance`: Explain when story-mode generation is available, when it can proceed without local GPU analysis, and when optional advanced analysis can improve output.

## Impact

- Backend services: `services.analysis`, `services.edit_orchestrator`, `services.edit_planner`, new story script generation/conversion services, provider configuration, and validation utilities.
- Data model: likely new story script artifact storage or a versioned JSON field tied to Project/Draft; no existing draft/render schema should be broken.
- API: project/draft endpoints for generating, retrieving, saving, and using story scripts; edit trigger accepts story mode.
- Frontend: ProjectAnalysis and ProjectEdit controls for Story/Narrato mode, script preview/editing, and novice-friendly progress/copy.
- Runtime: core story flow should not require GPU; optional local STT/object/tracking/emotion/MusicGen remains available as enhancement paths.
- Tests: service-level validation for StoryScript JSON, conversion to `CutPlan`, workflow gating, and UI/API contracts.
