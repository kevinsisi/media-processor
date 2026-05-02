# mobile-upload-ui (NEW)

## Purpose

Give the operator (mobile-first usage) a clean Traditional Chinese flow for creating a project and getting media + script in. Survives reload, no surprise data loss.

## Requirements

### REQ-1: New-project page

- Route `/projects/new` shows a single-column form.
- Fields: 名稱 (required), 客戶 (optional), 風格檔 (select; options driven by `profiles/*.yaml`), IG 輸出比例 (visual radio with three frames at the actual aspect ratio).
- "建立專案" CTA is disabled until 名稱 + 風格檔 + 比例 are set. On success, navigates to `/projects/{id}/upload`.

### REQ-2: Upload page

- Route `/projects/:id/upload` shows three sections in vertical stack:
  - **影片**: tap-to-pick or drag-drop area, multi-file. Each picked file gets its own row with filename, size, progress bar, and status (上傳中 / 已完成 / 已暫停).
  - **腳本**: paste textarea with character count + 上傳 `.txt` button. Auto-saves with debounce.
  - **完成度**: read-only summary card — 影片數量、是否有腳本、輸出比例。Includes a "返回專案清單" link.

### REQ-3: Reload survival

- Picked files are remembered via `localStorage` keyed by file fingerprint (`name:size:lastModified`) → `session_id`. After a reload the same file resumes its session.
- On mount, the page calls `GET /uploads/{session_id}` for each known session and reflects current state in the row.

### REQ-4: Mobile-first quality

- All interactive elements are ≥ 44 px tall.
- Base font 16 px; line-height ≥ 1.6 for Chinese readability.
- The primary CTA in each section is a sticky bottom bar on viewports < 768 px.
- The aspect-ratio picker uses visual frames so the user picks by shape, not by reading "9:16".
- All copy is Traditional Chinese (繁體中文).
