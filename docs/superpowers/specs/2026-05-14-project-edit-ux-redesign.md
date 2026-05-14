# ProjectEdit UI/UX Redesign — Design Spec

**Date:** 2026-05-14  
**Status:** Approved for implementation  
**Scope:** `web/src/pages/ProjectEdit.tsx` + child components  
**Version target:** 0.43.0

---

## Problem Statement

`ProjectEdit.tsx` has grown to 2,076 lines and accumulated four structural UX issues:

1. `EditSettingsBlock` appears in three separate state-machine branches (showInitial, showReady/advanced panel, showFallback) with different semantic intents but identical UI — creates maintainability burden and user confusion.
2. The `<details>` "進階微調" panel packs EditSettingsBlock + DraggableTimeline + SubtitleEditor into one collapse, making it information-overloaded and width-constrained.
3. The analysis-incomplete banner shows even when the draft is `ready`, creating false urgency.
4. The five render-option toggles are flat with no grouping; the stabilization toggle gives no hint about asset-level vs render-level interaction.
5. The main CTA is buried below all settings with no sticky presence and no visual differentiation between its four functional states.

---

## Decisions

### D1 — EditSettingsBlock appears in exactly two places

| State | Where settings live | CTA label |
|---|---|---|
| showInitial | Inline on page | 「▶ 產生 N 秒短影音」|
| showReady | Tab: ⚙ 設定 (inside the new 3-tab panel) | 「↺ 重新產生」|
| showFallback | Reuses showInitial layout — no separate block needed | 「▶ 產生 N 秒短影音」|

`showFallback` is analysis-failed retry. It renders the same JSX path as `showInitial`, so no third copy of the component.

### D2 — RenderOptions toggle grouping

Split the 5 toggles into two visual groups inside `EditSettingsBlock`:

**基本**
- 畫面防手震 — hint: `已使用穩定版的素材不會重複處理`
- 加上字幕
- 使用轉場效果

**AI 進階（選一種）**
- 自動跟住主角 — tooltip: 固定主角在畫面中心
- AI 智慧運鏡 — tooltip: AI 決定鏡頭移動方向

Mutual exclusion: enabling one disables the other (UI only — backend already handles this correctly). Show a small label "兩者互斥" beneath the group.

### D3 — Replace `<details>` with 3-Tab panel

`<details id="advanced-panel">` is replaced by a `<Tabs>` component with three tabs:

| Tab | Content | Width requirement |
|---|---|---|
| ⚙ 設定 | EditSettingsBlock (2nd occurrence) | normal |
| 🎞 時間軸 | DraggableTimeline | full-width |
| 💬 字幕 | SubtitleEditor | normal |

Tab state stored in `useState<'settings' | 'timeline' | 'subtitles'>`, defaulting to `'settings'`. Tab panel for 時間軸 removes any max-width constraint so DraggableTimeline can use full viewport width.

### D4 — Sticky Footer CTA

The generate/re-generate button moves to a `position: sticky; bottom: 0` footer bar. Four visual states:

| Condition | Appearance | Label |
|---|---|---|
| Normal (showInitial) | Blue filled | `▶ 產生 N 秒短影音` |
| Normal (showReady) | Secondary outline | `↺ 重新產生` |
| analysisBlocked | Gray disabled + tooltip | `分析尚未完成，請稍候` |
| triggering / showProcessing | Spinner + blue | `產生中…` + progress bar |
| showQueued | Amber filled | `排隊中 · 查看排隊 →` (opens QueueModal) |
| showFailed | Red outline | `↺ 重試` (same as existing retry button — showFailed's inline retry button is removed) |

The sticky footer is always in the DOM; its content and style swap via the existing state flags.

### D5 — Analysis banner scoping

The analysis-incomplete banner (`⏳ 分析尚未完成`) is shown only when:
```
showInitial && !project.analysis_complete
```

In `showReady` state, if `!project.analysis_complete` (edge case: old project), show a small inline badge on the VersionSwitcher chip instead of a full-width banner.

### D6 — VersionSwitcher status labels

Each draft version chip gets a status suffix:

| Draft status | Chip appearance |
|---|---|
| approved | green · `v1 ✓` |
| failed / rejected | muted · `v2 ✗` |
| current (latest) | blue filled · `v3 NOW` |
| ready_for_review | white · `v3` |

---

## Component Changes

### `ProjectEdit.tsx`

- Remove the third `EditSettingsBlock` render branch (showFallback case). Route showFallback to the same JSX block as showInitial.
- Replace `<details id="advanced-panel">` with `<AdvancedEditPanel activeTab={advancedTab} onTabChange={setAdvancedTab} ... />`.
- Add `const [advancedTab, setAdvancedTab] = useState<AdvancedTab>('settings')`.
- Move the CTA button to a `<StickyGenerateFooter>` component outside the state card, always rendered.
- Scope the analysis banner to `showInitial && !project.analysis_complete`.

### New: `StickyGenerateFooter.tsx`

```
props:
  state: 'idle' | 'blocked' | 'triggering' | 'queued' | 'ready'
  targetDuration: number
  onGenerate: () => void
  onStop: () => void
  onOpenQueue: () => void
  queuePosition?: number
```

Renders a `sticky bottom-0` bar with the CTA in the correct visual state.

The `AdvancedEditPanel` is rendered only in `showReady` state, immediately below the Publish Workbench block. It is not shown in showInitial, showProcessing, showQueued, showFailed, or showFallback.

### New: `AdvancedEditPanel.tsx`

```
props:
  activeTab: 'settings' | 'timeline' | 'subtitles'
  onTabChange: (tab) => void
  // passes through all EditSettingsBlock + DraggableTimeline + SubtitleEditor props
```

Tab bar on top, renders one of three panels below. Timeline panel removes `max-w-*` constraint from its wrapper.

### Modified: `RenderOptions.tsx`

- Add `<div class="toggle-group">` wrappers: one for 基本, one for AI進階.
- Add `hintText` prop to the 防手震 toggle: `「已使用穩定版的素材不會重複處理」`.
- Add mutual exclusion logic: when `autoReframe` is toggled on, set `smartCamera` to false (and vice versa). Emit a single `onChange` with both values.
- Add `「兩者互斥」` label below the AI進階 group.

### Modified: `VersionSwitcher.tsx`

- Accept `drafts: Draft[]` and compute status label per chip.
- Apply color classes based on `draft.status`.

---

## State Machine — No Changes

The existing `showInitial / showQueued / showProcessing / showReady / showFailed / showFallback` flags are unchanged. This redesign only changes which JSX renders in each state; it does not change transition logic or API calls.

---

## Files to Create / Modify

| Action | File |
|---|---|
| Modify | `web/src/pages/ProjectEdit.tsx` |
| Create | `web/src/components/StickyGenerateFooter.tsx` |
| Create | `web/src/components/AdvancedEditPanel.tsx` |
| Modify | `web/src/components/RenderOptions.tsx` |
| Modify | `web/src/components/VersionSwitcher.tsx` |

No backend changes. No API changes.

---

## Out of Scope

- 素材檢查頁 (③ analysis page) — separate redesign
- 專案清單 (① project list) — separate redesign
- Any render pipeline logic
- Mobile responsiveness (separate pass)
