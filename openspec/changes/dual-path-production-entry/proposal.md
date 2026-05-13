## Why

Operators approved the interactive redesign prototype, but the production flow still forces users through status-oriented pages before they can choose between a hands-off auto render and manual asset/clip control. The next development slice must move that dual-path decision into the real product so the approved direction is not trapped in `/prototype/redesign`.

## What Changes

- Add production ProjectList actions for the two primary paths:
  - one-click automatic first draft
  - manual asset and clip control through ProjectAnalysis
- Add Upload completion actions with the same split after assets/script are ready.
- Keep the interactive prototype route available as the visual reference while production pages adopt the first slice.
- Use the existing edit queue endpoint for one-click draft generation in this slice; later slices can replace it with a deeper wait-analysis/wait-stabilization orchestration endpoint.

## Capabilities

### New Capabilities

- `dual-path-production-entry`: Users can choose full-auto draft generation or manual control from real entry points, not only the prototype.

### Modified Capabilities

- `project-list-workflow`: Project cards expose next-step actions instead of only status/read-only labels.
- `upload-next-step`: Upload no longer has only one generic next step after assets are ready.

## Impact

- Frontend ProjectList and Upload UX.
- Frontend edit trigger path from non-edit pages.
- Roadmap/version/memory documentation for the first production slice.
