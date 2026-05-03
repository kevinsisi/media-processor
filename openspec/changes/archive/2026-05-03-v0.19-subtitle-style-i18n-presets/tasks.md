# Tasks — v0.19-subtitle-style-i18n-presets (0.19.0)

## 1. Subtitle style customisation

- [x] 1.1 Add six `subtitle_*` columns to `Project` (font / color /
  outline_color / position / size / outline_width).
- [x] 1.2 `alembic 0015_project_subtitle_style` — six new columns with
  server defaults matching the historic look.
- [x] 1.3 `SubtitleStyle` dataclass in `services/subtitles.py` packs
  the six values + maps them to drawtext args.
- [x] 1.4 `burn_subtitles` accepts `subtitle_style: SubtitleStyle | None`.
- [x] 1.5 `ProjectDetail` + `_project_detail` builder surface the six
  fields; `SubtitleStylePatch` body for `PATCH /projects/{id}/subtitle-
  style` is partial-update.
- [x] 1.6 `web/src/components/SubtitleStyleEditor.tsx` with live preview
  using 8-direction text-shadow to approximate drawtext borderw.
- [x] 1.7 Mount inside the `視覺疊加` settings group on `ProjectEdit`.

## 2. Clip-style presets

- [x] 2.1 `StylePresetParams` dataclass in `services/edit_planner.py`
  with min/max span, transition allowlist, default transition,
  bgm_hint, prompt_hint, irregular_lengths flag.
- [x] 2.2 Five preset bundles: `fast` / `slow` / `commercial` /
  `artistic` / `custom`.
- [x] 2.3 `resolve_style_preset(name)` falls back to `custom` on typo
  / unknown values.
- [x] 2.4 `_score_one_asset` splices `prompt_hint` into the per-asset
  Gemini prompt; `_assemble_plan` honours the transition allowlist.
- [x] 2.5 Music-suggestion endpoint reads the project's preset and
  splices `bgm_hint` into the BGM prompt.
- [x] 2.6 `Draft.style_preset` column + `alembic 0016_draft_style_preset`
  with a `CHECK` constraint covering the five literals.
- [x] 2.7 `EditTriggerRequest.style_preset` + `DraftSummary.style_preset`
  round-trip the choice.
- [x] 2.8 `web/src/components/StylePresetPicker.tsx` — five-card radio
  group with per-card BGM hint + span range + transition tags.

## 3. Re-introduce missing xfade variants

- [x] 3.1 Add `fade` / `dissolve` / `fadeblack` / `fadewhite` back to
  `VALID_TRANSITIONS` so the slow / artistic / commercial preset
  allowlists can emit them.
- [x] 3.2 Renderer's xfade chain accepts the four variants verbatim
  (they were valid ffmpeg names all along; only the planner allowlist
  had dropped them in v0.14.3).
- [x] 3.3 Verify `_coerce_legacy_transition` still maps unknown
  strings to `wipeleft` so old serialised plans don't crash on load.

## 4. Bilingual subtitles (en secondary track)

- [x] 4.1 Add `subtitle_secondary_lang` + `subtitle_secondary_segments_
  json` to `Asset`.
- [x] 4.2 Add `subtitle_secondary_text` to `DraftSegment`.
- [x] 4.3 `alembic 0017_secondary_subtitles` adds all three nullable.
- [x] 4.4 `services/translate_subtitle.py` runs Whisper task=`translate`
  on the asset's audio; persists segments to
  `Asset.subtitle_secondary_segments_json`.
- [x] 4.5 `POST /assets/{id}/translate-subtitle` endpoint enqueues the
  worker; idempotent (skips when `subtitle_secondary_lang` already
  set unless `force=true`).
- [x] 4.6 `burn_subtitles` accepts `secondary_srt_path`; renders a
  smaller drawtext layer above the primary cue band.
- [x] 4.7 `EditTriggerResponse` / `DraftDetail` surface secondary
  subtitle availability so the UI can show a toggle.

## 5. Alembic chain repair

- [x] 5.1 Re-chain `0015_project_subtitle_style.down_revision`
  to `"0014_project_watermark"` after parallel-branch merge.
- [x] 5.2 Re-chain `0016_draft_style_preset.down_revision` to
  `"0015_project_subtitle_style"`.
- [x] 5.3 Re-chain `0017_secondary_subtitles.down_revision` to
  `"0016_draft_style_preset"`.
- [x] 5.4 Verify `alembic upgrade head` runs clean against a fresh
  database (no "Multiple head revisions" error).

## 6. Tests

- [x] 6.1 Subtitle style: drawtext args matrix (every font × position ×
  size × outline width combination); hex colour validator rejects
  `#ggg` / `redblue` / etc.
- [x] 6.2 Style preset: span clamp matrix + transition allowlist
  enforcement; `custom` preserves legacy behaviour; unknown preset
  string falls back to `custom`.
- [x] 6.3 Bilingual: Whisper-translate fake mode emits expected SRT;
  `burn_subtitles` with `secondary_srt_path` produces two drawtext
  layers at the expected vertical offset.
- [x] 6.4 Alembic: `tests/unit/test_models.py` upgrade-to-head test
  passes against the re-chained migration sequence.
