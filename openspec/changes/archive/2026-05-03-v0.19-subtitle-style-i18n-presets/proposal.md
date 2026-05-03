## Why

After M9 (tracking + per-segment volume) shipped, three orthogonal
operator requests landed at once and the natural release boundary was
to bundle them — they all touch the renderer + the project settings
panel without colliding:

1. **Subtitle style is one-size-fits-all.** Every reel ships the same
   white-on-black Noto Sans CJK TC at the bottom. Brand-coloured runs
   (e.g. luxury car content) want a serif face, larger size, thicker
   outline; talking-head educational content wants smaller text with
   no outline. Forcing operators to hand-edit ffmpeg drawtext args is
   not a viable UX.

2. **Reels feel homogeneous regardless of content.** The Gemini cut
   planner produces a coherent rhythm but every project ends up with
   the same 4–6 s span / `wipeleft / slideright / circlecrop`
   transition palette. Operators want a one-click "make this fast" /
   "make this slow / artistic / commercial" lever that biases span
   bounds + transition allowlist + the BGM hint without forcing a
   per-asset re-prompt.

3. **English secondary subtitles.** Brand content for international
   distribution needs an English track alongside the zh-Hant one. The
   Whisper model already supports `task="translate"`; the only
   plumbing missing was a separate transcript column + a renderer
   that draws two drawtext layers (zh-Hant primary near the bottom,
   en secondary just above it).

The release is numbered **0.19.0** rather than four 0.18.x patches
because the alembic chain was rewritten in this batch — see Impact.

## What Changes

### 1. Subtitle style customisation

Six new columns on `Project` capturing the drawtext-equivalent style:

- `subtitle_font` (`noto_sans_tc` / `noto_sans_tc_bold` / `noto_serif_tc`)
- `subtitle_color` / `subtitle_outline_color` (hex like `#ffffff`,
  pattern-validated server-side)
- `subtitle_position` (`top` / `middle` / `bottom`)
- `subtitle_size` (`small` / `medium` / `large`, mapped to drawtext
  `fontsize` per render canvas)
- `subtitle_outline_width` (`none` / `thin` / `thick`, mapped to
  drawtext `borderw`)

Defaults match the historic look (white-on-black Noto Sans CJK TC
bottom-anchored medium thin), so a project that doesn't touch the
panel renders identically to pre-v0.19. `SubtitleStyle` dataclass
threads the values through `burn_subtitles` / `render` so the chain
stays clean. Migration `0015_project_subtitle_style` adds all six
with server defaults.

`PATCH /projects/{id}/subtitle-style` body is partial-update. The
new `SubtitleStyleEditor` component lives next to the
`WatermarkPicker` in the `視覺疊加` settings group with a live
preview that approximates drawtext output (8-direction text-shadow
stack mimics `borderw`).

### 2. Clip-style presets

Five named presets (`fast` / `slow` / `commercial` / `artistic` /
`custom`) bundling four parameters each:

- `min_span_ms` / `max_span_ms` — tightens or relaxes the planner's
  span clamp (e.g. fast = 3–5 s; slow = 8–15 s).
- `transition_allowlist` — restricts which xfade variants the
  planner can emit (e.g. fast = `wipeleft / slideright / circlecrop`;
  slow = `dissolve / fade / fadeblack`).
- `default_transition` — the fallback when Gemini returns something
  outside the allowlist.
- `bgm_hint` / `prompt_hint` — copy spliced into the music-suggestion
  prompt + the per-asset score prompt so the rhythm stays coherent.

`Draft.style_preset` snapshots the choice on the trigger so a
re-render reproduces the same biases. `custom` is the legacy
behaviour (current span clamp, full transition palette, no prompt
biases). The `StylePresetPicker` component on `ProjectEdit` is a
five-card radio group with each card showing the BGM hint, span
range, and transition tags inline.

This batch also re-introduced four xfade variants the v0.14.3 cleanup
had removed (`fade` / `dissolve` / `fadeblack` / `fadewhite`) so the
slow / artistic / commercial presets have something to use; the
`fast` / `custom` allowlists keep the post-v0.14.3 assertive set
verbatim.

Migration `0016_draft_style_preset` adds `drafts.style_preset`
(non-NULL string with `custom` server default + check constraint
covering the five values).

### 3. Bilingual subtitles (en secondary track)

- `Asset.subtitle_secondary_lang` (str | NULL) — marker for which
  secondary language has been generated; `"en"` when Whisper translate
  has been run.
- `Asset.subtitle_secondary_segments_json` — translated SRT-style
  segments (same shape as `AssetTranscript.segments_json`).
- `DraftSegment.subtitle_secondary_text` — per-cut snapshot of the
  clipped translation, written by the orchestrator at render time.

Stored on `Asset` (not `AssetTranscript`) so re-running STT doesn't
drop the translation and re-running translation doesn't touch the
zh-Hant. New `POST /assets/{id}/translate-subtitle` endpoint runs
the analysis worker; payload is `{lang: "en"}`.

`burn_subtitles` gains a `secondary_srt_path` parameter. When set
the renderer adds a second drawtext layer just above the primary
cue band (offset = primary `fontsize × 1.3`) at slightly smaller
size + reduced outline so the secondary feels like a translation
caption rather than competing with the primary.

Migration `0017_secondary_subtitles` adds the three columns nullable
so legacy assets / drafts pick up `NULL` and the renderer treats
them as "no secondary track" — no re-translation needed.

### 4. Alembic chain repair

The four 0.18.0 PRs (watermark, subtitle style, clip presets,
bilingual subs) were each developed on parallel worktree branches
that all minted the same next migration number (`0014_*`). Merging
them in sequence required re-numbering + re-chaining `down_revision`
links so `alembic upgrade head` doesn't fail with "Multiple head
revisions". Final order:

```
0013_draft_segment_volume
↓
0014_project_watermark        (was 0014 on the watermark branch)
↓
0015_project_subtitle_style   (was 0014 on the subtitle branch — re-chained)
↓
0016_draft_style_preset       (was 0014 on the preset branch — re-chained)
↓
0017_secondary_subtitles      (was 0015 on the bilingual branch — re-chained)
```

This is the rule home for the alembic-parallel-branch-merge memory.

## Impact

- **Renderer.** `burn_subtitles` accepts a `SubtitleStyle` dataclass
  and an optional `secondary_srt_path`; the planner consults
  `Draft.style_preset` for span / transition bias.
- **Schema.** Six new columns on `projects`, three on `assets`, one
  on `drafts`, one on `draft_segments`. Four new alembic migrations
  (0014–0017), all with safe defaults so legacy rows behave like
  pre-v0.19.
- **API contract.** `PATCH /projects/{id}/subtitle-style`,
  `POST /assets/{id}/translate-subtitle`, `EditTriggerRequest.
  style_preset`. `DraftSummary.style_preset`, `ProjectDetail.
  subtitle_*` round-trip the new state.
- **UI.** New `StylePresetPicker` + `SubtitleStyleEditor` components
  inside `ProjectEdit`. `BgmSourcePicker` reads `stylePreset` so its
  AI suggestion call carries the preset's BGM hint.
- **Backwards compat.** Existing rows without preset / style end up
  with `custom` / `noto_sans_tc / #ffffff / #000000 / bottom / medium /
  thin` defaults — rendered output is byte-similar to pre-v0.19.
