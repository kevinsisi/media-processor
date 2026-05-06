## Context

The current stack already supports upload, analysis, auto-edit, preview, export artifacts, timeline edits, subtitles, BGM, and watermarking. The target user for the next product slice is a non-technical beginner who wants many short videos for Instagram and Facebook, not a video editor or developer.

This change intentionally avoids new rendering architecture. It reorders the current UI around novice success and uses existing export APIs for IG/FB presets.

## Goals / Non-Goals

**Goals:**

- Make the completed draft page read as a social-ready workbench.
- Let users choose IG/FB destinations before raw aspect/resolution.
- Preserve script edits before moving from upload to analysis.
- Remove the stale Review entry point from normal routing.
- Replace high-visibility technical copy with beginner-facing language.
- Keep all UI in Traditional Chinese.

**Non-Goals:**

- Do not implement direct publishing to Instagram/Facebook in this slice.
- Do not add multi-draft batch generation yet.
- Do not redesign the advanced timeline editor.
- Do not replace every technical string in the repository; focus on the main novice path.

## Decisions

- Use platform preset cards in `ExportSheet` backed by current `{aspect, height}` payloads.
  - Rationale: no backend migration or new contract is needed; `DraftExportArtifact` already persists downloads.
  - Alternative rejected: add a platform column immediately. That is better for future publish packages but unnecessary for the first UI slice.
- Keep the existing raw aspect/resolution controls as an advanced section.
  - Rationale: expert flexibility remains available without forcing beginners to understand ratios first.
- Add upload script flushing in the existing page state instead of route-level blocking middleware.
  - Rationale: the only unsafe path today is the upload page's next action; this is the smallest correct fix.
- Redirect Review with React Router rather than deleting the component immediately.
  - Rationale: preserves code history and avoids breaking imports while eliminating the user-facing stale route.

## Risks / Trade-offs

- Platform presets are UI-only metadata for now -> Mitigation: follow-up publish package spec can persist platform intent.
- Advanced controls remain present -> Mitigation: collapsed/secondary presentation keeps the novice path primary.
- Script flush can fail before navigation -> Mitigation: keep the user on upload and show the existing script error.
- Some technical strings remain in less-traveled admin/advanced areas -> Mitigation: document a later full copy cleanup pass.
