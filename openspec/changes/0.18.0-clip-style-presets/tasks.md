# Tasks — 0.18 clip-style presets

## Backend
- [x] Add `ClipStylePreset` StrEnum + `CLIP_STYLE_PRESET_VALUES` to `models/enums.py`; export from `models/__init__.py`.
- [x] Add `Draft.style_preset` column + CHECK constraint (`models/draft.py`).
- [x] Alembic migration `0014_draft_style_preset` (depends on `0013_draft_segment_volume`).
- [x] Add `StylePresetParams` dataclass + 5 preset bundles + `resolve_style_preset()` helper to `services/edit_planner.py`.
- [x] Extend `VALID_TRANSITIONS` in both `services/edit_planner.py` and `services/video_renderer.py` to include `fade / dissolve / fadeblack / fadewhite`.
- [x] Plumb `style: StylePresetParams` through `_build_asset_prompt`, `_parse_asset_score`, `_score_one_asset`, `_assemble_plan`, `_extended_span`.
- [x] Add `style_preset: str = "custom"` to `services/edit_planner.plan(...)`, `services/edit_orchestrator.run_render(...)`, `services/edit_orchestrator._plan_stage(...)`, `services/queue.enqueue_project_edit(...)`, `workers/edit_jobs.render_draft(...)`.
- [x] Add `style_preset` to `EditTriggerRequest` and `DraftSummary` (Pydantic).
- [x] Persist `style_preset` on the new `Draft` row in the trigger endpoint (`api/routers/projects.py`).
- [x] Surface `style_preset` on the draft serializers in `api/routers/drafts.py` + `api/routers/projects.py`.
- [x] `services/music_suggest.suggest(...)` accepts `style_hint: str = ""`; injected into the prompt template.
- [x] `GET /projects/{id}/music-suggestion?style_preset=...` resolves the preset and passes its `bgm_hint`.

## Frontend
- [x] Add `ClipStylePreset` type alias + extend `EditTriggerRequest` + `DraftSummary` in `web/src/api/types.ts`.
- [x] `apiClient.fetchMusicSuggestion(projectId, stylePreset?)` appends `?style_preset=...` when set.
- [x] New `StylePresetPicker` component (5 cards) inline in `web/src/pages/ProjectEdit.tsx`.
- [x] Render `StylePresetPicker` in both initial and ready edit cards.
- [x] Wire `stylePreset` state into `triggerProjectEdit` payload + `BgmSourcePicker` prop.
- [x] CSS for `.style-preset-picker` / `.style-preset-card` in `web/src/pages/ProjectEdit.css`.

## Verification
- [x] `py -m ruff check` — no new errors on touched files.
- [x] `npm run build` — passes.
