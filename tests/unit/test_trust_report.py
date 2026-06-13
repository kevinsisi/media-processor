from __future__ import annotations

from media_processor.services.trust_report import (
    DraftTrustReport,
    TrustEvidenceMetric,
    new_trust_report,
    trust_report_from_json,
    trust_summary_from_json,
)


def test_planned_report_serializes_summary_and_stage_outcomes() -> None:
    report = new_trust_report()
    report.add_stage("render", "success", evidence=[TrustEvidenceMetric("output_written", True)])

    payload = report.to_dict()
    parsed = trust_report_from_json(payload)

    assert parsed is not None
    assert parsed.summary.to_dict() == {
        "status": "planned",
        "degradation_count": 0,
        "highest_severity": None,
    }
    assert parsed.stage_outcomes[0].stage == "render"
    assert parsed.stage_outcomes[0].evidence[0].to_dict()["value"] is True


def test_degraded_report_records_stable_event_fields() -> None:
    report = new_trust_report()
    report.add_degradation(
        "story_tts",
        "story_tts_partial_failure",
        "部分旁白產生失敗，已使用字幕-only fallback。",
        fallback_used="subtitles_only",
        evidence=[TrustEvidenceMetric("failed_items", 2, unit="items")],
    )

    payload = report.to_dict()
    parsed = DraftTrustReport.from_dict(payload)

    assert parsed.summary.status == "degraded"
    assert parsed.summary.degradation_count == 1
    assert parsed.summary.highest_severity == "warning"
    assert payload["degradation_events"][0]["stage"] == "story_tts"
    assert payload["degradation_events"][0]["fallback_used"] == "subtitles_only"


def test_failed_report_records_failing_stage_and_error() -> None:
    report = new_trust_report()
    report.mark_failed("render", "ffmpeg failed before output write")

    payload = report.to_dict()
    parsed = DraftTrustReport.from_dict(payload)

    assert parsed.summary.status == "failed"
    assert parsed.failing_stage == "render"
    assert parsed.error_message == "ffmpeg failed before output write"
    assert parsed.stage_outcomes[0].status == "failed"


def test_missing_report_summarizes_as_unknown() -> None:
    summary = trust_summary_from_json(None)

    assert summary.to_dict() == {
        "status": "unknown",
        "degradation_count": 0,
        "highest_severity": None,
    }


def test_unavailable_metric_does_not_emit_synthetic_value() -> None:
    metric = TrustEvidenceMetric(
        "tracking_lost_frame_ratio",
        available=False,
        message="tracking data unavailable",
    )

    payload = metric.to_dict()

    assert payload["available"] is False
    assert "value" not in payload
