# transcript-editor-ui (NEW)

## Purpose

Mobile-first React page that surfaces every analysis result for a project's assets, lets the operator edit the transcript inline with autosave, and polls the API for in-flight analysis so progress is visible without manual refresh. All copy is Traditional Chinese.

## Requirements

### REQ-1: Route and entry point

- A new route `/projects/:id/assets` renders the project analysis page.
- The "進入素材分析" CTA on `/projects/:id/upload` navigates here.
- `ProjectList` shows a `分析中` status chip for any project where at least one asset has `status='analyzing'`.
- Each row in `ProjectList` is a tap target that navigates to `/projects/:id/assets` regardless of the project's status (fixes the operator-reported bug where `pending` / `analyzing` rows had no clickable entry point). The pre-existing status-cell CTAs (`檢視`, `開啟`) keep their distinct destinations and visually-stronger affordance, but they no longer constitute the only way to enter a project.

### REQ-1b: Date column rendering on the project list

- `formatCreatedAt(iso)` returns `YYYY/MM/DD\nHH:MM` (slash-separated date and 24-hour time on two physical lines, joined by `\n`).
- `.entry__num-when` declares `white-space: pre-line` so the newline renders at every breakpoint — fixing the operator-reported truncation where the previous middle-dot-joined single line clipped to `2026-05:...16:39` at narrow column widths.

### REQ-2: Per-asset card

- Each asset is rendered as a card (single column on < 600 px viewports). Card contents:
  - Header row: filename + duration (mm:ss) + status pill (`待分析 | 分析中 | 已分析 | 分析失敗`).
  - Per-step chip row: `轉錄 / 場景 / 運鏡 / 對稿`, each chip showing one of `pending | running | done | failed` (localised: `等待 | 進行中 | 完成 | 失敗`).
  - Expandable transcript section (collapsed by default).
  - Tag chip cluster: scene tags (with localised names: `室內 | 室外 | 棚拍 | 特寫 | 中景 | 全景 | 動態 | 靜態 | 明亮 | 昏暗 | 混合光`). Only fired tags appear.
  - Motion timeline: thin horizontal bar coloured by motion class (`pan / tilt / zoom / static / handheld`), with timestamp tooltips on tap/hover. Horizontally scrollable on < 600 px.
  - Coverage card: `照稿 76 % · 即興 24 %` rendered as a 2-segment progress bar with localised label.
  - Card footer: `重新分析` button (calls `POST /assets/{id}/analyze` without `force`) and `強制重跑` (calls with `force=true`, requires confirm dialog).

### REQ-3: Inline transcript edit + autosave

- The expanded transcript renders one row per segment: `[mm:ss → mm:ss]` timestamp prefix + a `<textarea>` containing the segment text.
- Editing a textarea:
  - Updates local state immediately (optimistic).
  - Marks the segment chip as `已編輯` until autosave completes.
  - Schedules a debounced save 1.5 s after the last keystroke for that segment. Autosave POSTs the full updated transcript via `PUT /assets/{id}/transcript`.
  - Shows a small `儲存中…` indicator during the request and `已儲存 hh:mm` after success. On error: red `儲存失敗，重試中` + automatic retry with exponential backoff (1 s, 3 s, 10 s, then surface the failure to the user with a manual retry button).
- Persistence is explicit and DB-backed: a hard reload restores the saved segments. Edits are never kept only in localStorage.

### REQ-4: Polling cadence

- A `useAssetPolling(projectId)` hook fetches `GET /projects/:id` (which embeds per-asset analysis status):
  - Initial load: 1 immediate fetch.
  - While any asset has `status='analyzing'`: every 3 seconds.
  - Once all assets are settled (`analyzed | analysis_failed`): every 10 seconds for 1 minute, then stop.
  - Pressing `重新分析` resets the cadence to 3 s.
- The hook returns `{assets, pollIntervalMs, refresh, isPolling}`. The page shows a subtle `更新中` indicator only while `isPolling` is true and the page is visible.

### REQ-5: Failure-class chips

- A failed step chip shows a localised summary based on the `failed:{reason}` token:
  - `gpu-unavailable` → `GPU 不可用`
  - `quota-exhausted` → `配額耗盡，稍後再試`
  - `model-error:*` → `模型錯誤`
  - `disk-error:*` → `儲存錯誤`
  - `timeout` → `逾時`
  - `missing-script` → `缺少腳本`
- Tapping/clicking a failed chip opens a tooltip showing the raw reason string for ops debugging.

### REQ-6: Mobile-first specifics

- All interactive controls have ≥ 44 px touch targets.
- Textareas use 16 px font, 1.6 line-height for Chinese readability.
- Tag chips wrap; motion timeline is horizontally scrollable.
- Layout collapses to single column at < 600 px.

### REQ-7: zh-Hant copy

- All visible strings in the new page and the polling hook's status messages are Traditional Chinese.
- The fixed scene/motion tag enums map to Traditional-Chinese display names via a `web/src/i18n/tags.ts` module (single source of truth, no inline duplication).
