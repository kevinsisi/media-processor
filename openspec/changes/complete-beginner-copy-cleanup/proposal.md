## Why

The main v0.28.2 path now avoids the loudest technical terms, but secondary pages, settings, errors, and empty states still expose editing/backend language that makes the app feel like an expert cockpit. A beginner-first IG/FB shorts workflow needs consistent outcome-oriented Traditional Chinese copy across the app, especially on mobile.

## What Changes

- Audit visible web UI copy across upload, analysis, edit, export, queue/status, settings, asset tracking, subtitles, BGM, watermark, and error/empty states.
- Replace user-facing technical terms with beginner-friendly outcome language while keeping precise diagnostics available where helpful.
- Keep advanced controls available but label and group them as optional fine-tuning rather than the default workflow.
- Standardize CTA, status, loading, failed, retry, and done wording so users understand what to do next.
- Do not change API behavior, rendering behavior, database schema, or platform export formats.

## Capabilities

### New Capabilities
- `beginner-copy-system`: User-facing copy must support a novice IG/FB short-video workflow with consistent Traditional Chinese terminology, clear next actions, and technical language hidden or softened outside advanced/debug contexts.

### Modified Capabilities

None.

## Impact

- Affected code: `web/src/**/*.tsx`, `web/src/**/*.ts`, relevant CSS only when layout needs to support revised copy length.
- Documentation: `README.md`, `ROADMAP.md`, and project memory should record the final terminology rules.
- Verification: frontend build plus targeted UI text review; no backend migration expected.
