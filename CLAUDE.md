# Project Rules

This repository is a GitHub template. When a new project is generated from it, these rules activate immediately so any AI coding assistant follows the same workflow conventions from the first commit.

Edit this file freely to add stack-, domain-, or team-specific rules for your project. Keep the Skill Activation section so the bundled `skills/` and `.github/skills/` stay wired in.

## Global Working Rules

- Read the current code, files, and runtime context before deciding on a change.
- Prefer the smallest correct fix over broad refactors.
- Fix root causes, not only visible symptoms or display-layer effects.
- When the best next step is already clear, execute it instead of asking redundant confirmation.
- Do not send the user through intermediate debugging steps you can perform directly.
- Do not use regex to parse structured formats when explicit parsing or a proper parser is more reliable.
- For new projects, major features, rewrites, or redesigns with unresolved decisions, present a reviewable plan before writing product code.
- Parallelize independent work when it meaningfully reduces turnaround; keep the main thread focused on coordination and synthesis.
- Frame each task clearly with the actual problem, constraints, and expected end state.
- Do not replace user intent with hardcoded fallback values after a failure.
- Retry transient external or AI failures with backoff; when retries are exhausted, surface the real failure.
- Add per-item timeouts to batched external calls so one slow request does not block the whole batch.
- Keep user keywords and search intent unchanged unless the user explicitly asked for transformation.
- Verify behavior in a real runnable environment whenever feasible.
- Do not claim CI, CD, deployment, or runtime success from guesswork; use trustworthy evidence.
- When a code change is complete, treat follow-through as part of the work, not an optional extra.
- Every code change must update memory, update spec, commit, and push unless the user explicitly says not to.
- Prefer commit-first, push-later batching for larger work groups when repeated pushes would only retrigger CI/CD without adding review value.
- If a requirement should govern future implementation, write it into the formal rule sources instead of leaving it only in chat context.
- Avoid magic numbers in implementation; prefer existing enums, or introduce named constants when no enum exists.
- Before commit, confirm AI-generated methods, classes, and files are actually used; remove unused junk instead of committing it.
- Build checks before commit must use the repo's concrete command(s), not vague "validation" language.
- For any non-trivial feature request or requirement, first confirm requirements with the user and define OpenSpec before implementation.
- For major changes, use a brainstorming step before proposal or implementation.

## Skill Activation Rules

Treat the following skill files as active workflow rules for this workspace, even if the host AI environment does not expose them through a built-in skill registry. Apply them automatically by task type:

- Treat `skills/execution-style/SKILL.md` as the default execution behavior for normal implementation work
- Treat `skills/plan-before-build/SKILL.md` as mandatory for new projects, major features, and large redesigns before implementation begins
- Treat `skills/project-stack-standard/SKILL.md` as mandatory when choosing or reviewing app/service stack, backend setup, database choice, or monorepo structure
- Treat `skills/root-cause-debugging/SKILL.md` as mandatory for bug investigation and regressions
- Treat `skills/integration-robustness/SKILL.md` as mandatory for AI calls, external APIs, retries, and batched integrations
- Treat `skills/verification-and-evidence/SKILL.md` as mandatory when reporting runtime, CI, CD, or deployment status
- Treat `skills/agent-design/SKILL.md` as mandatory for multi-agent or tool-enabled agent architecture work
- Treat `skills/completion-checklist/SKILL.md` as mandatory for any code change before reporting completion
- Treat `skills/deployment/SKILL.md` as mandatory for deployment, Docker, reverse-proxy, CI/CD, and release work
- Treat `skills/frontend-design/SKILL.md` as mandatory for frontend creation or redesign work
- Treat `skills/key-pool-standard/SKILL.md` as mandatory for any AI key-pool, quota, or multi-key retry implementation
- Treat `skills/skill-creator/SKILL.md` as the active workflow when creating, improving, or evaluating a skill
- Treat `.claude/skills/superpowers/using-superpowers/SKILL.md` as the bootstrap rule for cross-cutting workflow skills (load this before deep code work)
- Treat `.claude/skills/superpowers/brainstorming/SKILL.md` as mandatory before any new feature proposal or major redesign
- Treat `.claude/skills/superpowers/writing-plans/SKILL.md` and `.claude/skills/superpowers/executing-plans/SKILL.md` as the canonical plan + execution loop for multi-step features
- Treat `.claude/skills/superpowers/test-driven-development/SKILL.md` as the default when adding service-level Python logic with tests (orchestrator stages, planners, mixers)
- Treat `.claude/skills/superpowers/systematic-debugging/SKILL.md` as mandatory whenever a render / Whisper / Gemini failure mode is unclear
- Treat `.claude/skills/superpowers/verification-before-completion/SKILL.md` as mandatory before claiming any deploy / render result is correct
- Treat `.claude/skills/superpowers/using-git-worktrees/SKILL.md` as the reference when the user explicitly asks to start a worktree
- Treat `skills/gemini-prompts/asset-score/SKILL.md` as the canonical reference for the per-asset scoring prompt (`edit_planner._ASSET_SCORE_PROMPT`)
- Treat `skills/gemini-prompts/scene-tag/SKILL.md` as the canonical reference for the Vision tagging prompt (`scene_tagging._VISION_PROMPT`)
- Treat `skills/gemini-prompts/script-coverage/SKILL.md` as the canonical reference for the script-vs-transcript coverage prompt (`script_coverage._PROMPT_TEMPLATE`)
- Treat `skills/gemini-prompts/llm-patcher/SKILL.md` as the canonical reference for the M5 profile-patch prompt (`llm_patcher._SYSTEM_PROMPT`)
- Treat `.github/skills/openspec-explore/SKILL.md` as the active workflow when the user wants exploration without implementation
- Treat `.github/skills/openspec-propose/SKILL.md` as the active workflow when creating a new OpenSpec change
- Treat `.github/skills/openspec-apply-change/SKILL.md` as the active workflow when implementing an OpenSpec change
- Treat `.github/skills/openspec-archive-change/SKILL.md` as the active workflow when archiving a completed OpenSpec change

Mirror locations (`.claude/skills/`, `.gemini/skills/`, `.opencode/skills/`, `.github/skills/`) hold the same OpenSpec workflow skills so Claude Code, Gemini CLI, opencode, and GitHub Copilot all see them. The canonical source for general workflow skills lives in `skills/`.

## Persistent Standards

- Every code change must update memory (if applicable), update OpenSpec (if applicable), commit, and push; larger work batches may commit in checkpoints and push once the batch is ready. Rule home: `skills/completion-checklist/SKILL.md`.
- Complex tasks must carry workflow checkpoints in the task list, and major task boundaries must trigger a fresh rule check. Rule home: `skills/execution-style/SKILL.md` and `skills/completion-checklist/SKILL.md`.
- Any requirement that should govern future implementation must be written into the formal rule sources (this file or a skill), not left only in chat context. Rule home: `skills/execution-style/SKILL.md`.
- Any non-trivial feature request should first go through an exploration/confirmation step and be captured in OpenSpec before implementation.

## Project Architecture Pointers

CLAUDE.md is meta-rules; concrete project state lives elsewhere. When you need to understand what's currently in the codebase, prefer in this order:
- `ROADMAP.md` — Phase 6–10 全程路線圖（已完成/規劃中），含每個 sub-task 驗收標準。新對話開頭先讀這個就能對齊大方向。**目前版本：0.23.7。**
- `openspec/changes/` — current in-flight proposals + tasks. Completed milestones live under `openspec/changes/archive/YYYY-MM-DD-<name>/`. Archived through 0.23.x: M6 0.12.0 / M7 0.13.0 / M8 0.14.0 / M8.1 0.14.x / **v0.18 watermark / v0.19 subtitle-style + i18n + presets / v0.20 timeline + UX / v0.21 transitions + BGM + subject_class / v0.22 UI/UX 收斂 / v0.23 pixel-precise point tracking**.
- The auto-memory index at `~/.claude/projects/D--GitClone--HomeProject-media-processor/memory/MEMORY.md` — non-obvious deploy / runtime quirks (Tailscale routing, GPU runtime, drafts/BGM storage, key pools, MusicGen, vidstab, YOLO tracking, alembic parallel-branch hazard, render flag persistence).
- `skills/gemini-prompts/` — 4 個 reusable Gemini prompt skill（asset-score / scene-tag / script-coverage / llm-patcher），改 prompt 前先看這裡。
- The code itself — render pipeline:
  - `services/edit_planner.py` per-asset Gemini fanout (M6) + emotion / motion / face fields on `_AssetScore` + `_assemble_plan` 3-pass dedup/top-up (M8.1) + `StylePresetParams` + 5-preset bundles (v0.19) + `_subject_presence_range_ms` / `_apply_subject_filter` for `Project.subject_class` (v0.21.0; A=drop / B=snap)
  - `services/video_renderer.py` xfade chain (v0.19 re-introduces fade / dissolve / fadeblack / fadewhite for slow / artistic / commercial presets; transitions=None gates plain-mux concat) + drawtext subtitle burn-in (`SubtitleStyle` dataclass v0.19; secondary track v0.19.4) + `_zoompan_filter` (d=1, gated on motion-OR-face) + auto-reframe sendcmd chain (v0.16) + per-asset tracking-target dispatch (v0.17) + `apply_watermark` 9-grid PNG overlay (v0.18). v0.23.4: `_cut_segment` returns bool indicating whether the dynamic `crop@reframe` chain was applied; `cut_segments` returns `(paths, reframed_flags)`; `stabilize_segments` takes `skip_indexes` so vidstab does NOT re-translate already-subject-stabilised segments
  - `services/auto_reframe.py` Kalman-smoothed YOLO bbox → ffmpeg sendcmd dynamic crop (v0.16; tuned Q=120 R=80 MAX_DELTA=24 CROP_ZOOM_FACTOR=0.75 in v0.16.1) + `compute_crop_path_from_custom_roi` for CSRT user ROI (v0.17) + `compute_crop_path_from_point_track` for LK pixel-precise tracking (v0.23.0; synthesises 1×1 bbox so the existing centre-of-bbox math works unchanged). v0.23.5: `write_sendcmd_file` packs x AND y into ONE directive per timestamp (`crop@reframe x N, crop@reframe y M;`) — splitting them onto two lines at the same start_time triggers a ffmpeg 4.4 dispatcher bug that silently drops the second-onward directive at ≥30 Hz, freezing the crop at its initial value
  - `services/object_tracking.py` YOLOv8n at 5 fps; v0.17 keeps multi-class tracks + adds `track_custom_roi` (OpenCV CSRT); v0.21.0 adds `aggregate_detected_classes` (project-level class roll-up for the SubjectClassPicker dropdown)
  - `services/point_tracking.py` (v0.23.0) pyramidal Lucas-Kanade single-pixel tracker; bidirectional pass from init time (forward + backward); freezes at last good position on `lost`; opencv-python-headless required in BOTH api and worker images because the `tracking-target` endpoint runs LK synchronously. v0.23.7: takes `init_norm_x` / `init_norm_y` and resolves to pixels from cv2's POST-rotation dims (`CAP_PROP_FRAME_WIDTH/HEIGHT` with `CAP_PROP_ORIENTATION_AUTO=1`). Never multiply norms by `Asset.resolution` for tracking — that's the raw stream dims and disagrees with the thumbnail / cv2 frame on rotated assets (e.g. iPhone / DJI portrait clips stored as landscape + `rotate=90`).
  - `services/bgm_mixer.py` voice-ducked BGM stage (M6.4) + per-segment voice/BGM gain via `SegmentVolume` + `apply_voice_volume` no-BGM fallback (v0.17)
  - `services/musicgen.py` AI BGM generation (v0.15.x — fp32 forward + CFG step-down chain + transcript-aware prompt suggestion)
  - `services/vidstab.py` two-pass digital stabilization (v0.14.3)
  - `services/subtitles.py` builds drawtext-burned cues with `TRANSITION_OVERLAP_MS` accounting (M6.1 / M7.2)
- The code itself — schema + API surface:
  - `models/project.py` — `bgm_path` (M6.4), `watermark_*` (v0.18, alembic 0014), `subtitle_*` (v0.19, alembic 0015), `subject_class` (v0.21.0, alembic 0018)
  - `models/draft.py` — `cut_plan_json` / `bgm_path` (v0.16.2) / `style_preset` (v0.19, alembic 0016) / `render_flags_json` (v0.21.1, alembic 0019 — snapshot of trigger-time toggle states for skip-plan re-renders)
  - `models/asset.py` — `tracking_json` (v0.16) / `tracked_object_index` + `custom_roi_json` (v0.17, alembic 0012) / `subtitle_secondary_*` (v0.19, alembic 0017) / `point_tracking_json` + `point_tracking_origin` (v0.23.0, alembic 0021); `tracked_object_index = -4` is the new sentinel for "use point_tracking_json"
  - `api/routers/drafts.py` — segment-level CRUD endpoints (v0.20.0 split / patch / delete + `_reflow_segments_and_cut_plan`); skip-plan re-render endpoints accept `render_flags` body override (v0.21.3); `_draft_render_flags(draft, override)` resolves per-flag with priority body > snapshot > all-True
  - `api/routers/projects.py` — `_project_detail` is the single canonical builder (v0.20.3 fold of duplicate); `subject_class` PATCH + `detected-classes` GET (v0.21.0)

## When To Remove Or Replace Skills

- Remove `skills/frontend-design/` if the project has no frontend.
- Remove `skills/key-pool-standard/` if the project does not use AI API keys.
- Remove `skills/agent-design/` if the project is not building AI agents.
- Keep `skills/execution-style/`, `skills/completion-checklist/`, `skills/plan-before-build/`, `skills/root-cause-debugging/`, `skills/verification-and-evidence/`, and `skills/integration-robustness/` for any project.
- If you delete a skill, also delete its line in the Skill Activation Rules above.
