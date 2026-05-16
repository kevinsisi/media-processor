## Context

Media Processor is now positioned as a mobile-first Traditional Chinese tool for novices producing IG / FB short videos. v0.28.2 cleaned the primary path, but visible UI copy still leaks technical implementation details in secondary controls, status cards, modals, and error states. This change is intentionally frontend-heavy and should not alter render behavior.

## Goals / Non-Goals

**Goals:**
- Establish a consistent beginner-facing terminology system across the web UI.
- Keep the default workflow outcome-oriented: upload, check material, generate a short, export for IG / FB, download/share.
- Preserve expert controls while making them visually and linguistically secondary.
- Keep diagnostic detail available in advanced/error contexts without making the main path intimidating.

**Non-Goals:**
- No backend status model changes.
- No new export presets, direct posting, thumbnails, or batch generation.
- No redesign of major layout beyond copy-length support.

## Decisions

- Use a terminology map rather than one-off rewrites. Replace raw terms consistently: render -> generate/export depending on context, worker/RQ/queue -> processing line/status, YOLO/auto-reframe -> keep subject centered, vidstab -> stabilize image, FFmpeg -> video processing, Gemini/LLM -> AI selection or AI suggestion.
- Keep code comments and internal TypeScript/API names technical. Only user-visible labels, aria labels, help text, toast messages, empty states, and error copy are in scope.
- Preserve precise failure reasons when they help support, but lead with a calm human summary. Detailed raw errors can remain in advanced details or monospace diagnostic fields.
- Prefer existing component structure. Add shared text constants only when repeated labels would otherwise drift.

## Risks / Trade-offs

- [Risk] Softer copy may hide useful troubleshooting details. -> Mitigation: keep raw details in secondary diagnostic text and preserve logs/API payloads.
- [Risk] Longer Traditional Chinese labels may cause mobile overflow. -> Mitigation: include mobile layout checks and adjust CSS only where copy length requires it.
- [Risk] Broad copy changes can touch many files. -> Mitigation: make no behavior changes and use frontend build/type checking plus targeted review.
