## Context

`media-processor` currently behaves as a production-oriented editing pipeline: uploads become Assets, analysis produces transcripts/tags/tracking data, the edit planner selects source spans, and the existing renderer produces drafts with subtitles, BGM, transitions, Smart Camera, stabilization, watermarking, exports, and review workflow. This is strong for asset management and rendering, but the planning layer is still mostly a clip-scoring system.

NarratoAI demonstrates a complementary model that is more effective for short-form narration: convert subtitles and sampled frames into a structured story script first, then render from that script. Its useful core is not the Streamlit UI or MoviePy merge path; it is the StoryScript contract: timestamped source ranges, picture descriptions, narration, and an OST field that determines whether a segment uses generated narration, original audio, or both.

The desired direction is to make `media-processor` perform closer to NarratoAI in analysis, subtitles, script generation, and short-form assembly while retaining the existing production renderer and review surface.

## Goals / Non-Goals

**Goals:**

- Add a Story/Narrato mode that can generate short-form scripts from existing transcripts, uploaded subtitles, and optional sampled-frame analysis.
- Keep the core Story/Narrato path usable without GPU by relying on provider-based ASR, vision, text generation, and CPU ffmpeg rendering.
- Introduce a versioned StoryScript schema that can be validated, persisted, edited, regenerated, and converted into the existing `CutPlan` / `DraftSegment` shape.
- Preserve the current render pipeline as the source of truth for cutting, concatenation, subtitles, BGM, watermark, Smart Camera, stabilization, exports, and review.
- Make room for later TTS narration and narration-aware duration planning without forcing that complexity into the first implementation.

**Non-Goals:**

- Do not embed NarratoAI's Streamlit UI, task state, Docker runtime, or MoviePy final merge engine.
- Do not require local GPU analysis for the first Story/Narrato mode implementation.
- Do not replace standard, luxury-auto, viral-short, manual reorder, subtitle edit, or export workflows.
- Do not implement TTS narration audio in the first phase unless explicitly scoped by a later change.

## Decisions

### Decision: Treat NarratoAI as a story-planning pattern, not a runtime dependency

The integration should reproduce the useful capabilities as native `media-processor` services instead of shelling out to NarratoAI or importing its app runtime.

Alternatives considered:

- Directly call NarratoAI as a sidecar: faster prototype, but duplicates storage, state, config, task tracking, and rendering.
- Copy NarratoAI services verbatim: keeps behavior close, but imports timestamp-string-centric code and MoviePy assumptions that conflict with `media-processor`'s millisecond DB model and ffmpeg renderer.

Rationale: Native services keep one pipeline, one DB, one review UI, and one renderer.

### Decision: Add a StoryScript artifact before `CutPlan`

Story mode should produce a validated StoryScript before rendering. A StoryScript item uses integer millisecond ranges internally and carries fields such as `asset_id`, `source_start_ms`, `source_end_ms`, `picture`, `narration`, `audio_intent`, `beat_type`, `hook_type`, and `reason`.

`audio_intent` should be an enum rather than NarratoAI's numeric `OST` values:

- `narration`: generated narration replaces/overrides original audio intent.
- `original`: keep source audio as the content beat.
- `narration_with_original`: narration and original audio are both intended.

Alternatives considered:

- Store NarratoAI JSON directly in `Script.body`: easy, but mixes human-uploaded project scripts with generated edit artifacts.
- Generate `CutPlan` directly from prompt output: simpler, but loses the editable story/narration layer that makes NarratoAI valuable.

Rationale: StoryScript gives the operator and later phases an explicit script layer without disturbing the renderer contract.

### Decision: Convert StoryScript into existing `CutPlan` / `DraftSegment`

The first renderable implementation should adapt StoryScript items into `CutPlanSegment` values and persist them through the existing `_persist_plan()` path or a sibling persistence path that writes equivalent `DraftSegment` rows.

Rationale: This preserves existing renderer, subtitles, Smart Camera, BGM, export, manual segment editing, and review behavior.

### Decision: Make provider-based cloud/lightweight analysis first-class

Story mode should not depend on local Whisper, YOLO, MediaPipe, or emotion analysis. It should use whichever inputs exist:

- Existing `AssetTranscript` rows from local Whisper or external ASR.
- Uploaded subtitle content converted to transcript-like segments.
- Optional sampled-frame descriptions from external vision providers.
- Existing local tags/tracking as enhancement signals when available.

Rationale: The user wants NarratoAI-like output quality, and NarratoAI's useful story flow does not require a local GPU. Local AI remains valuable for privacy, cost control, tracking, auto-reframe, and MusicGen, but it should not block story generation.

### Decision: Split TTS narration into a later phase

The first Story/Narrato mode should generate story scripts and renderable plans, but not require generated narration audio. TTS support requires audio-file lifecycle, provider selection, real-duration measurement, BGM/original/narration mixing rules, and timeline extension behavior. Those should be introduced after the StoryScript and conversion path are stable.

Rationale: TTS is high-value but also the highest-risk timeline change. The NarratoAI bug work showed that hard-clipping narration causes broken output, while letting narration extend the video requires deliberate timeline semantics.

## Proposed Flow

1. User selects Story/Narrato mode from ProjectAnalysis or ProjectEdit.
2. Backend gathers existing transcripts/subtitles and optional visual analysis artifacts.
3. Story script generator calls configured text provider with a Narrato-style short-form prompt.
4. Service validates and repairs the JSON into StoryScript schema.
5. StoryScript is persisted and returned to the UI for preview/editing.
6. User can render from StoryScript.
7. Backend converts StoryScript to `CutPlan` / `DraftSegment` and runs existing render stages.
8. Existing draft preview/download/export/rebuild-subtitles paths continue to work.

## Risks / Trade-offs

- Prompt output drift -> Use strict JSON schemas, schema_version checks, bounded ranges, enum validation, and fallback repair that fails loudly when invalid.
- Story scripts overfit short-drama language -> Use profile/edit-mode prompt blocks so carsmeet/luxury/commercial outputs stay restrained.
- External vision/ASR cost -> Cache story analysis artifacts and cap sampled frames per asset/cut.
- Timeline mismatch when TTS is added later -> Store source duration and intended narration text now, but defer audio-duration timeline expansion to a separate change.
- UI complexity -> Start with preview/edit/regenerate actions and keep advanced JSON details secondary.
- Existing workflows regress -> Add Story/Narrato mode as a parallel path; do not change default edit mode behavior.

## Migration Plan

1. Add schema/service code behind a new Story/Narrato mode flag with no default behavior change.
2. Add persistence for story artifacts with schema versioning, or use a clearly versioned JSON column/artifact path if a table migration is deferred.
3. Add API endpoints and UI surfaces that are inactive unless the operator chooses Story/Narrato mode.
4. Add tests for JSON validation, conversion to `CutPlan`, and no-GPU generation path.
5. Deploy with existing standard edit mode unchanged; rollback by hiding/disabling the Story/Narrato mode entry points.

## Open Questions

- Should first-phase persistence be a new table (`story_scripts`) or a versioned file artifact referenced by Draft/Project?
- Which provider should be default for external ASR in production: existing local Whisper, Fun-ASR, Whisper API, or uploaded subtitles only?
- Should Story/Narrato mode initially support multi-asset projects only, single long videos only, or both?
- Should generated StoryScript be editable as structured cards immediately, or begin with read-only preview plus JSON fallback?
