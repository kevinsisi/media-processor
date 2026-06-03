## 1. Schema And Persistence

- [x] 1.1 Define StoryScript schema dataclasses/types with `schema_version`, story item source ranges, narration, picture, audio intent, beat metadata, and validation errors
- [x] 1.2 Decide and implement first-phase persistence for StoryScript artifacts as a new table or versioned project/draft artifact path
- [x] 1.3 Add database migration if table/column persistence is selected
- [x] 1.4 Add JSON validation and repair utilities that reject invalid ranges, unknown audio intents, duplicate order values, and missing required fields

## 2. Story Generation Service

- [x] 2.1 Implement input gathering from `AssetTranscript`, uploaded subtitle-like text, and existing analysis artifacts without requiring GPU-only steps
- [x] 2.2 Add a Narrato-style short-form prompt builder with profile/edit-mode context and Traditional Chinese output guidance
- [x] 2.3 Implement provider-backed StoryScript generation using existing OpenCode/Gemini/text-provider configuration patterns
- [x] 2.4 Persist generation metadata including provider, model, source inputs used, and whether visual context was included
- [x] 2.5 Add failure handling that surfaces actionable reasons instead of creating invalid render artifacts

## 3. StoryScript To Render Plan

- [x] 3.1 Implement StoryScript-to-`CutPlan` conversion using integer millisecond ranges and current `CutPlanSegment` contracts
- [x] 3.2 Map `audio_intent=original` to retained source-audio draft segments
- [x] 3.3 Map `audio_intent=narration` and `narration_with_original` to renderable visual segments and subtitle cues without requiring TTS in phase one
- [x] 3.4 Preserve StoryScript reason/beat metadata in draft plan metadata or segment reason fields where useful for review UI
- [x] 3.5 Add tests proving converted plans can be persisted as `DraftSegment` rows and rendered through the existing renderer path

## 4. API And Workflow Integration

- [x] 4.1 Add API endpoint to generate or regenerate a StoryScript for a project
- [x] 4.2 Add API endpoint to fetch and save/edit the current StoryScript artifact
- [x] 4.3 Extend edit trigger schemas to accept Story/Narrato mode without changing existing standard/luxury/viral behavior
- [x] 4.4 Wire `edit_orchestrator.run_render()` to use StoryScript conversion when Story/Narrato mode is requested
- [x] 4.5 Ensure existing skip-plan, subtitle rebuild, export, and review flows continue to work for story-mode drafts

## 5. Frontend UX

- [x] 5.1 Add ProjectAnalysis guidance for Story/Narrato readiness based on transcript/subtitle availability and optional analysis status
- [x] 5.2 Add Story/Narrato mode action using beginner-friendly Traditional Chinese copy
- [x] 5.3 Add StoryScript preview UI with ordered story items, source range, narration, picture, and audio intent
- [x] 5.4 Add regenerate and render-from-story actions while keeping existing one-click generation available
- [x] 5.5 Keep advanced JSON/debug details visually secondary to publishing-oriented actions

## 6. Verification

- [x] 6.1 Add unit tests for StoryScript validation and invalid model outputs
- [x] 6.2 Add unit tests for transcript/subtitle input gathering when GPU-backed analysis is missing
- [x] 6.3 Add unit tests for StoryScript-to-`CutPlan` conversion and audio-intent mapping
- [x] 6.4 Add API tests for generate/fetch/save/render story-mode endpoints
- [x] 6.5 Add frontend tests or focused build checks for Story/Narrato mode controls and readiness copy
- [x] 6.6 Run the repository's backend tests and frontend build/test commands before implementation is considered complete

## 7. Deferred Follow-Up Scope

- [x] 7.1 Document TTS narration audio as a separate follow-up change, including provider selection, real audio duration measurement, and narration-aware timeline extension
- [x] 7.2 Document optional sampled-frame story visual analysis as an enhancement if it is not included in the first implementation slice
- [x] 7.3 Document GPU-heavy local analysis as optional enhancement mode, not a blocker for core Story/Narrato generation
