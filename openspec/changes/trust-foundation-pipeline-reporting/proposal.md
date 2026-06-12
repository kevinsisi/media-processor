## Why

Media Processor currently treats many pipeline failures as acceptable fallbacks and still presents the draft as a successful output. This erodes operator trust because a generated video can silently lose AI selection, frame analysis, TTS narration, tracking, stabilization, BGM, or smart-camera quality without a visible explanation.

## What Changes

- Introduce a trust report for every draft render that records stage outcomes, degradation events, evidence metrics, and whether the final output matched the requested plan.
- Replace silent fallback behavior with explicit outcomes: hard failure for required stages, or recorded degradation for allowed fallback paths.
- Surface trust status in API responses and the review UI so operators can distinguish `ready_for_review` outputs that are fully planned from outputs with degraded stages.
- Add measurable evidence for key reliability-sensitive stages, including stabilization quality, tracking lost-frame ratio, AI/heuristic plan source, frame-analysis coverage, TTS/narration coverage, subtitle timing source, and render/mix completion.
- Stop writing fabricated success artifacts such as fake frame-analysis JSON marked `done`; unavailable analysis must be terminally failed or explicitly degraded.
- Keep existing fallback paths only when they are explicitly configured as allowed and are visible in the draft trust report.

## Capabilities

### New Capabilities

- `pipeline-trust-reporting`: Defines render-stage trust reports, degradation events, evidence metrics, and API/UI visibility for whether a draft can be trusted.

### Modified Capabilities

- `job-lifecycle-reliability`: Draft render lifecycle must distinguish real success, terminal failure, and successful output with recorded degradations.
- `workflow`: One-click and manual edit flows must show trust/degradation outcomes after generation instead of presenting every completed draft as equally successful.

## Impact

- Affected backend: `edit_orchestrator.py`, frame analysis, stabilization/asset variant services, point/object tracking, Story/Narrato TTS, BGM mixer, draft schemas/API routers, migrations/models for persisted trust reports or degradation events.
- Affected frontend: ProjectEdit progress/review panels, ProjectAnalysis decision hub, draft status summaries, queue/progress copy.
- Affected operations: CI tests must cover failure and degradation paths; production operators get explicit evidence instead of silent fallback outputs.
