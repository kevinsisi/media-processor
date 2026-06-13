## 1. Trust Report Data Contract

- [x] 1.1 Add draft trust report storage with nullable migration and ORM field or one-to-one model.
- [x] 1.2 Create typed trust report dataclasses/builders for status, stage outcomes, degradation events, and evidence metrics.
- [x] 1.3 Add serialization/deserialization helpers and unit tests for planned, degraded, failed, and unknown reports.
- [x] 1.4 Extend draft API schemas to expose trust summary and trust report details while keeping old drafts compatible.

## 2. Backend Pipeline Instrumentation

- [x] 2.1 Instrument edit orchestration to initialize, update, and persist a trust report for every new render attempt.
- [x] 2.2 Replace silent plan-generation fallback with explicit failure or recorded `plan_generation` degradation events.
- [x] 2.3 Replace fabricated frame-analysis success artifacts with failed/unavailable state and coverage evidence.
- [x] 2.4 Record Story/Narrato TTS coverage, failed item counts, fallback policy, and subtitle timing source in trust events.
- [x] 2.5 Record stabilization selection evidence, including active variant source and available jitter metrics when present.
- [x] 2.6 Record tracking/smart-camera outcomes, including lost-frame ratio when available and static fallback events when used.
- [x] 2.7 Record BGM/audio mix and final render/mux stage outcomes, failing hard for required output stages.

## 3. API And Frontend Visibility

- [x] 3.1 Show trust summary on ProjectEdit latest draft/progress/review surfaces.
- [x] 3.2 Add a degradation detail panel grouped by stage with user-facing Traditional Chinese messages.
- [x] 3.3 Update one-click generation completion copy to distinguish planned, degraded, failed, and unknown outputs.
- [x] 3.4 Update manual render/re-render copy to avoid false success language when trust status is degraded or unknown.

## 4. Tests And Verification

- [x] 4.1 Add backend unit tests for required-stage failure vs optional-stage degraded fallback.
- [x] 4.2 Add backend unit tests that frame-analysis provider failure is not stored as fake successful JSON.
- [x] 4.3 Add API tests for trust summary/report fields on new and old drafts.
- [x] 4.4 Add frontend tests or build-time coverage for trust banner rendering states.
- [x] 4.5 Run `py -m pytest tests/unit -q`, `py -m ruff check src tests`, `py -m ruff format --check src tests`, `py -m mypy src`, and web build checks.
- [x] 4.6 Verify production CI/CD and run at least one production render smoke that demonstrates planned or degraded trust reporting.
