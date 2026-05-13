## Context

ProjectAnalysis already computes whether all assets are terminal and whether a latest draft exists, but it only uses that information to change the main CTA label. Operators still have to infer whether they should wait, edit now, or preview the latest draft.

## Goals / Non-Goals

**Goals:**

- State the next action in plain language near the hero area.
- Distinguish between analysis readiness and optional stabilization readiness.
- Avoid blocking editing when only stabilization is still in progress.

**Non-Goals:**

- No backend changes.
- No new modal or wizard.
- No changes to render/stabilization behavior, only guidance copy.

## Decisions

- Compute the message from existing frontend state: `latestDraft`, `allAssetsTerminal`, and counts of `stabilization_status` values.
- Render the guidance as a dedicated hero callout rather than overloading error/status banners.
- Prefer direct action-first language: "可以開始製作剪輯", "可以先預覽成品", "若想等全部防抖版...".

## Risks / Trade-offs

- Too much copy could crowd the hero. Mitigation: keep the message to 1-2 short sentences.
- Guidance could become stale if polling lags. Mitigation: reuse the existing asset polling source of truth.
