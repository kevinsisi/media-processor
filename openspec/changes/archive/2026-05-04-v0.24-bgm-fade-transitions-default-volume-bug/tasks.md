# Tasks — v0.24.0 (BGM fade + transitions default + voice_volume bug)

## 1. BGM tail-fade

- [x] 1.1 `Project.bgm_fade_out_sec: Mapped[float]` non-nullable column with `default=3.0` / `server_default="3.0"`. Alembic `0022_project_bgm_fade_out` chains after `0021_asset_point_tracking`.
- [x] 1.2 `services.bgm_mixer._probe_video_duration_s(video_path) -> float | None` — best-effort ffprobe of the container duration. 10 s timeout, returns `None` on any failure so the mix still ships without the fade.
- [x] 1.3 `services.bgm_mixer.mix_bgm` gains `fade_out_sec: float = 0.0` kwarg. When `> 0` AND duration probe succeeds, appends `,afade=t=out:st={start}:d={dur}` onto the BGM track inside the existing filter_complex graph. `fade_dur = min(fade_out_sec, duration)`, `fade_start = max(0, duration - fade_dur)`.
- [x] 1.4 `services.edit_orchestrator.run_render` passes `project.bgm_fade_out_sec` through to `mix_bgm`.
- [x] 1.5 `ProjectDetail.bgm_fade_out_sec: float = 3.0` schema field; `_project_detail()` propagates the value through every endpoint that returns ProjectDetail.
- [x] 1.6 `BgmFadeOutPatch` schema (single field `fade_out_sec: float = Field(..., ge=0.0, le=10.0)`).
- [x] 1.7 `PATCH /projects/{project_id}/bgm-fade-out` endpoint returning the updated ProjectDetail.
- [x] 1.8 Frontend `apiClient.patchProjectBgmFadeOut(projectId, fadeOutSec)` method.
- [x] 1.9 `web/src/components/BgmFadeOutSlider.tsx` + `.css`. Slider 0..5 s, step 0.5; commits on `mouse-up` / `touch-end` / `key-up` so a drag fires one PATCH not eleven. Mounted inside the existing 配樂 SettingsGroup in `ProjectEdit.tsx`.
- [x] 1.10 Verified: re-render of draft 42 at v0.24.0 produces last-3-s audio at -33.8 dB mean / -18.7 dB max (down from -27.7 / -14.2 in the first 30 s); last-0.5-s drops to -45.7 dB mean — clear linear-ish taper consistent with `afade=t=out` over 3 s.

## 2. transitions=False default

- [x] 2.1 `EditTriggerRequest.transitions: bool = False` (was `True`) in `api/schemas.py`.
- [x] 2.2 `services.queue.enqueue_project_edit(transitions: bool = False)` default.
- [x] 2.3 `workers.edit_jobs.render_draft(transitions: bool = False)` default.
- [x] 2.4 `services.edit_orchestrator.run_render(transitions_enabled: bool = False)` default.
- [x] 2.5 `services.video_renderer.render(transitions_enabled: bool = False)` default.
- [x] 2.6 `web/src/pages/ProjectEdit.tsx` `useState<boolean>(false)` for `transitionsOn`.
- [x] 2.7 `_draft_render_flags` (drafts router) — legacy fallback replaced from "all-True default" with explicit per-flag `legacy_defaults = {"transitions": False, "stabilize": True, "subtitles": True, "auto_reframe": True}` dict so a re-rendered legacy draft picks up the new `transitions=False` behaviour.

## 3. voice_volume=0 silent-drop fix

- [x] 3.1 `services.edit_orchestrator._load_segment_volumes` — replace `float(getattr(r, "voice_volume", 1.0) or 1.0)` with explicit `None`-check `float(raw_vv) if raw_vv is not None else 1.0`. Same shape for `bgm_volume` (was already correctly None-checked there).
- [x] 3.2 `api.routers.drafts.serialise_draft_detail` — same `or 1.0` form, same fix. Pre-fix the GET endpoint returned `1.0` to the FE even when the DB row held `0.0`, so the slider showed 100 % regardless of the saved override; fix means the FE state matches DB state.
- [x] 3.3 Codebase rule added to `CLAUDE.md`: any nullable numeric column whose valid range includes `0` / `0.0` / `False` must use `value if value is not None else default`, never `value or default`. Memory entry `v024_bgm_fade_transitions_volume_bug.md` flags `services.edit_orchestrator` line 924 (`watermark_opacity or 1.0`) as the same trap that's still unfixed because no UI currently exposes opacity=0.
- [x] 3.4 Verified: re-render of draft 42 (all 11 segments at `voice_volume=0`) at v0.24.0 — audio mean drops from -26.9 dB → -27.9 dB, max from -12.0 dB → -14.2 dB. Voice silenced; remaining audio is pure BGM.

## 4. Memory + docs + version bumps

- [x] 4.1 `memory/v024_bgm_fade_transitions_volume_bug.md` — new memory file. Front-loaded with the voice_volume bug because that's the one a future debugging session needs to find first.
- [x] 4.2 `memory/MEMORY.md` index entry.
- [x] 4.3 `memory/project_media_processor_v2.md` snapshot bumped to 0.24.0.
- [x] 4.4 `ROADMAP.md` — new Phase 9.9 row + 9.9.1/9.9.2/9.9.3 subsections; current-version line; M10 deferred to 0.25.x+.
- [x] 4.5 `CLAUDE.md` — current-version line; archive list bumped through 0.24; `services/bgm_mixer.py` pointer mentions `fade_out_sec`; new "no `value or default` for nullable numeric" rule under Global Working Rules.
- [x] 4.6 Version bumped to 0.24.0 in `pyproject.toml` + `src/media_processor/api/main.py` + `web/package.json`.
- [x] 4.7 Branched as `claude/v0.24.0-bgm-fade-transitions-default-volume-bug`, merged --no-ff into `main`, pushed; docker compose build + up -d on the dispatch host; `/health` smoke-tested at 0.24.0; alembic 0022 confirmed in DB. Branch pruned local + remote after merge.
