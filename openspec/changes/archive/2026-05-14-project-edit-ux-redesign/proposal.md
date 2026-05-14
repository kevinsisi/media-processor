## Why

`ProjectEdit.tsx` had grown to 2,076 lines with three duplicated `EditSettingsBlock` branches, a single overloaded `<details>` advanced panel, flat unorganised render-option toggles, and a CTA buried at the bottom with no sticky presence — creating maintainability burden and user confusion.

## What Changes

- Split render toggles into **基本** group (stabilize, subtitles, transitions) and **AI進階** group (autoReframe, smartCamera) with mutual exclusion and `兩者互斥` label.
- Add stabilization hint text: `「已使用穩定版的素材不會重複處理」` on the render-level 防手震 toggle.
- Replace `<details id="advanced-panel">` with a `<AdvancedEditPanel>` 3-tab component (⚙ 設定 / 🎞 時間軸 / 💬 字幕).
- Move the generate / re-generate CTA to a `<StickyGenerateFooter>` `position: sticky; bottom: 0` bar with 6 visual states.
- Merge `showFallback` JSX path into `showInitial` — removes the third `EditSettingsBlock` copy.
- Scope analysis-incomplete banner to `showInitial && !analysisComplete` only.
- Add `VersionSwitcher` status chips: `approved` green ✓, `failed/rejected` muted ✗, latest blue `NOW`.

## Capabilities

### New Capabilities

- `sticky-generate-footer`: CTA is always visible regardless of scroll position, with state-driven appearance (idle / blocked / triggering / queued / failed / ready).
- `advanced-edit-panel`: DraggableTimeline gets full-width tab; SubtitleEditor and EditSettingsBlock each have a dedicated tab.

### Modified Capabilities

- `render-options-grouping`: Toggles are now grouped with mutual exclusion enforced at both React state and persist-layer handler levels.
- `version-switcher-status`: Draft status is visually surfaced per chip.

## Impact

- New files: `web/src/components/StickyGenerateFooter.tsx`, `web/src/components/StickyGenerateFooter.css`, `web/src/components/AdvancedEditPanel.tsx`, `web/src/components/AdvancedEditPanel.css`.
- Modified: `web/src/pages/ProjectEdit.tsx`, `web/src/pages/ProjectEdit.css`.
- No backend changes. No API changes. No schema migrations.
