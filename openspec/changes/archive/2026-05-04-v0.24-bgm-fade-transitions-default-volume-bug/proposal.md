## Why

Three small operator-feedback items shipped together because they
all touch the auto-edit re-render flow and there's no value in
spreading them across three separate releases:

1. **BGM cuts hard at the end of the reel.** Music stops cold at
   the last frame, which reads as jarring on a 30-second short. The
   operator wants a 3-second fade-out by default, with the option
   to dial it down to a hard cut for the historical behaviour or up
   to 5 s for slower reels.

2. **Transitions toggle defaults to ON, but every operator turns it
   OFF as their first action.** The `transitions=True` default
   dates back to v0.14.4 when xfade was new and we wanted operators
   to see it. Three releases of feedback later: hard cuts read
   tighter on car / product reels, and the toggle is more noise
   than signal when it's pre-checked. Default flipped; style
   presets that explicitly want xfade (slow / artistic /
   commercial) still re-enable on the trigger panel.

3. **`voice_volume = 0` was silently dropped.** Reported as: "I
   pulled all 11 segments to 0 % and re-rendered, but the voice
   was still audible." This is the load-bearing bug of the bundle
   and the reason the three items ship together — the same flow
   that the BGM fade option lives in (`bgm_mixer.mix_bgm`) is the
   one that processes `voice_volume`, and writing the bug fix
   inside the same release as the new feature means we only restart
   containers once.

## What Changes

### 1. BGM tail-fade (new feature)

#### Schema
- `Project.bgm_fade_out_sec`: `Mapped[float]` non-nullable with
  ``default=3.0`` / ``server_default="3.0"``. Alembic
  ``0022_project_bgm_fade_out``. Range bounded 0..10 s server-side
  (FE slider exposes 0..5 s as the common range).

#### Service
- `services.bgm_mixer.mix_bgm` gains ``fade_out_sec: float = 0.0``
  kwarg. When `> 0`:
  - Probe video duration via ffprobe (10-second timeout, gracefully
    returns `None` on any failure so the mix still ships without
    the fade).
  - Compute ``fade_dur = min(fade_out_sec, duration)`` and
    ``fade_start = max(0, duration - fade_dur)``.
  - Append ``,afade=t=out:st={start}:d={dur}`` to the BGM track
    inside the existing filter_complex graph
    (``[1:a]volume=...{fade}[bgm]``).
- `_probe_video_duration_s(video_path)` helper added (private; not
  worth its own module).

#### Orchestrator
- `services.edit_orchestrator.run_render` reads
  ``project.bgm_fade_out_sec`` and passes through to ``mix_bgm``.

#### API
- `ProjectDetail.bgm_fade_out_sec: float = 3.0` schema field.
- `_project_detail()` propagates the value.
- New `BgmFadeOutPatch` schema (single field
  ``fade_out_sec: float`` with ``ge=0.0, le=10.0``).
- New endpoint `PATCH /projects/{project_id}/bgm-fade-out`.
- New client method `apiClient.patchProjectBgmFadeOut(projectId, fadeOutSec)`.

#### Frontend
- New ``<BgmFadeOutSlider>`` component (TSX + CSS) mounted inside
  the existing 配樂 SettingsGroup right under ``<BgmSourcePicker>``.
  Slider 0..5 s, step 0.5 s; commits on `mouse-up` / `touch-end` /
  `key-up` so a drag through the range fires one PATCH not eleven.
  Local draft state mirrors the input so the slider feels
  responsive while the request is in flight.

### 2. transitions=False as the new default

Flipped in 6 places (one place per layer of the call stack):

- `EditTriggerRequest.transitions: bool = False` (was `True`)
- `services.queue.enqueue_project_edit(transitions=False)` default
- `workers.edit_jobs.render_draft(transitions=False)` default
- `services.edit_orchestrator.run_render(transitions_enabled=False)`
- `services.video_renderer.render(transitions_enabled=False)`
- `web/src/pages/ProjectEdit.tsx` ``useState<boolean>(false)`` for
  the local `transitionsOn`

Plus a 7th change: `_draft_render_flags` legacy fallback (pre-
v0.21.1 drafts with no snapshot) replaced from "all-True default"
with an explicit per-flag ``legacy_defaults = {"transitions": False,
"stabilize": True, "subtitles": True, "auto_reframe": True}`` dict.
A legacy draft re-rendered today picks up the new
``transitions=False`` behaviour, matching what the FE shows for
fresh projects.

### 3. voice_volume=0 silent-drop fix

#### Trace path
1. User pulled all 11 ``DraftSegment.voice_volume`` to `0` via
   the per-segment volume PATCH endpoint.
2. DB row holds `0.0` ✓ (verified by direct psql).
3. User triggers re-render. Worker picks up the job,
   ``run_render`` calls ``_load_segment_volumes(draft_id)``.
4. `_load_segment_volumes` build:
   ```
   voice_volume=float(getattr(r, "voice_volume", 1.0) or 1.0)
   ```
   For `voice_volume = 0`, this evaluates ``0 or 1.0`` → ``1.0``.
   Python's `or` returns the first truthy operand; `0` is falsy.
5. ``SegmentVolume(voice_volume=1.0)`` reaches the mixer.
6. `_build_voice_volume_expr` skips segments where
   ``voice_volume == 1.0``. With every segment at 1.0, the entire
   list reduces to ``"1.0"`` (the no-op constant).
7. Voice plays at original gain. User hears their voice.

The same `or 1.0` form lived in
``api.routers.drafts.serialise_draft_detail``, so the GET endpoint
returned `1.0` to the FE even when DB held `0.0`. The FE slider
showed 100 %; the user thought their previous 0 % had been
discarded.

#### Fix
Replace the `or 1.0` idiom with explicit `None`-check in both
places:
```
raw_vv = getattr(r, "voice_volume", None)
voice_volume=float(raw_vv) if raw_vv is not None else 1.0
```
Same shape for ``bgm_volume`` (already correctly None-checked
elsewhere; we only touched ``voice_volume``).

#### Verification
Re-rendered draft 42 (all 11 segments at `voice_volume=0`) at
v0.24.0:
- Pre-fix v17.mp4: mean -26.9 dB / max -12.0 dB (voice + BGM)
- Post-fix v17.mp4: mean -27.9 dB / max -14.2 dB (BGM only)

Voice is genuinely silent now. The 1 dB mean drop / 2 dB max drop
is consistent with voice contributing energy in the un-muted case.

## Impact

- **Schema**: alembic 0022 adds a non-null float column with a
  server default; existing rows pick up the default automatically.
- **API**: new endpoint, new schema, and `ProjectDetail` gains one
  field. Backwards compatible — older clients ignore the new field;
  new endpoint is additive.
- **Behavioural**: ``transitions=True`` no longer the default for
  fresh projects, fresh re-renders, or legacy drafts without a
  flag snapshot. Operators who genuinely want transitions on still
  toggle the FE switch on the trigger panel; their preference
  snapshots into ``Draft.render_flags_json`` so re-renders honour
  it. Style presets that hard-code transitions on still work.
- **Bug fix**: ``voice_volume = 0`` now actually silences segments.
  No data migration needed — the DB rows were always correct; only
  the loader and the GET serialiser were dropping the value.

## Codebase rule (added to CLAUDE.md)

The `value or default` idiom for nullable numeric columns where `0`
is a valid input is now banned by convention. Use
``value if value is not None else default``. The watermark_opacity
and other uses of `or 1.0` elsewhere in the codebase are NOT yet
fixed (low impact today because no UI exposes the falsy value), but
they're flagged in the v0.24 memory entry for the next person who
touches them.
