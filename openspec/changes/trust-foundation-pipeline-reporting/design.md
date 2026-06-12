## Context

The render pipeline has accumulated many recovery paths: AI planning can fall back to heuristics, frame analysis can synthesize placeholder analysis, Story/Narrato TTS can skip failed rows, smart camera can fall back to static framing, stabilization can skip or fail, and BGM/subtitle stages can degrade. These are useful operational tools, but today they are not consistently represented in durable state or in the review UI. A draft can become `ready_for_review` even when the output differs materially from the requested plan.

The change must preserve the operator's ability to get a usable video when optional stages fail, while making the result auditable. The core product contract becomes: successful outputs are either fully planned or visibly degraded with evidence.

## Goals / Non-Goals

**Goals:**

- Persist a per-draft trust report that records stage outcomes, degradation events, and evidence metrics.
- Make hard failures and allowed degradations explicit in backend APIs and frontend review/progress UI.
- Remove fake success artifacts, especially placeholder frame-analysis JSON marked as `done`.
- Provide enough metrics to explain why a generated video is trustworthy or degraded: stabilization quality, tracking loss, AI vs heuristic source, frame-analysis coverage, TTS coverage, subtitle timing source, and render/mix status.
- Keep fallback behavior available only when a stage is optional or fallback is explicitly allowed by request/configuration.

**Non-Goals:**

- Replacing LK/YOLO tracking, vidstab, smart-camera planning, or NarratoAI script generation algorithms.
- Redesigning the one-click mode selection UX beyond showing trust/degradation outcomes.
- Building full per-frame visual debugging tools or local correction workflows; those belong to the tracking reliability phase.
- Introducing external observability infrastructure.

## Decisions

### Decision: Store trust reports as draft-owned JSON first

Add a draft-owned trust report structure, stored either in a new `Draft.trust_report_json` column or a one-to-one table if migration review shows JSON growth is a concern. Use JSON first because the report is draft-scoped, append-only after render completion, and consumed as a document by the UI.

Alternatives considered:

- A normalized `draft_degradation_events` table. This is easier to query globally but adds migration and join complexity before the product contract is proven.
- Log-only reporting. This is insufficient because operators and API clients need durable review evidence.

### Decision: Separate stage outcome from draft terminal status

Keep existing draft terminal statuses for compatibility, but add a trust summary with `status = planned | degraded | failed`. A draft can remain `ready_for_review` while trust status is `degraded`, but it MUST expose the degradation count and reasons.

Alternatives considered:

- Add a new draft status such as `ready_with_degradations`. This is more explicit but risks broad frontend/API compatibility churn. It can be added later if the trust summary is not prominent enough.

### Decision: Record typed degradation events

Each degradation event should include `stage`, `severity`, `code`, `message`, `fallback_used`, and optional `evidence`. Stage names should be stable enum-like strings such as `frame_analysis`, `plan_generation`, `stabilization`, `tracking`, `story_tts`, `subtitle_timing`, `smart_camera`, `bgm_mix`, and `render`.

Alternatives considered:

- Free-form messages only. This is fast to implement but prevents stable UI grouping and tests.

### Decision: Required stages fail hard; optional stages degrade visibly

The orchestrator should decide at each stage whether failure is terminal or degraded based on render flags and mode. Examples: final render/mux failure is terminal; disabled subtitles are not a degradation; requested Story/Narrato TTS failure is a degradation only when `story_narration_fallback=true`; required frame analysis for documentary script generation should fail or downgrade to a recorded non-documentary fallback, not fabricate analysis.

Alternatives considered:

- Fail every stage. This is honest but would make optional enhancements too brittle for production use.
- Allow every fallback. This preserves today's behavior but does not rebuild trust.

### Decision: Evidence is best-effort but never fabricated

Metrics should be recorded when available and marked unavailable when not. The system MUST NOT write synthetic values that imply analysis succeeded. Examples: frame-analysis coverage is `done_count / asset_count`; stabilization evidence records pre/post jitter only if computed; tracking evidence records lost-frame ratio only if tracking data exists.

Alternatives considered:

- Require every metric before a draft can finish. That creates new failure modes and blocks useful outputs.

### Decision: UI starts with summary-first reporting

ProjectEdit and review surfaces should show a concise trust banner first: fully planned, degraded with count, or failed. A detail panel can list stage events and metrics. This minimizes UI churn while making degradation impossible to miss.

Alternatives considered:

- Add a separate report page. This hides the most important signal away from the draft review moment.

## Risks / Trade-offs

- [Risk] JSON report schema drifts across pipeline stages. → Mitigation: centralize report dataclasses/builders and unit-test serialization.
- [Risk] Operators see too many warnings and ignore them. → Mitigation: group events by stage and reserve high severity for output-changing degradations.
- [Risk] Existing fallback tests expect `ready_for_review` only. → Mitigation: keep terminal draft status compatible and update tests to assert trust status separately.
- [Risk] Re-render from existing drafts lacks historical trust data. → Mitigation: missing report is displayed as `unknown` for old drafts; new renders always produce a report.
- [Risk] Strict frame-analysis behavior could reduce documentary success rate. → Mitigation: allow a recorded fallback to Story mode only when explicitly acceptable and visible in the trust report.

## Migration Plan

- Add nullable trust-report storage so existing drafts remain readable.
- Backfill is not required; old drafts display trust status `unknown`.
- Deploy backend first with report generation hidden or defaulted, then expose frontend trust banners once API fields are present.
- Rollback is safe because the new storage is additive and existing status fields remain unchanged.

## Open Questions

- Should the final user-facing label be `degraded`, `partial`, or a localized Traditional Chinese phrase such as `有降級`?
- Should a future release promote `ready_for_review` with high-severity degradations into a distinct draft status?
- Which stabilization quality metrics are already available enough to expose in phase one, and which should be marked unavailable until the stabilization state-machine cleanup?
