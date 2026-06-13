"""Draft trust report data contract and JSON helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TypeAlias, cast

TrustStatus: TypeAlias = Literal["planned", "degraded", "failed", "unknown"]
TrustSeverity: TypeAlias = Literal["info", "warning", "error"]
StageStatus: TypeAlias = Literal[
    "pending", "success", "degraded", "failed", "skipped", "unavailable"
]

SEVERITY_RANK: dict[TrustSeverity, int] = {"info": 0, "warning": 1, "error": 2}


@dataclass(slots=True)
class TrustEvidenceMetric:
    """One measured or explicitly unavailable evidence value."""

    name: str
    value: Any = None
    available: bool = True
    unit: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "available": self.available,
        }
        if self.available:
            data["value"] = self.value
        if self.unit is not None:
            data["unit"] = self.unit
        if self.message is not None:
            data["message"] = self.message
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrustEvidenceMetric:
        return cls(
            name=str(data["name"]),
            value=data.get("value"),
            available=bool(data.get("available", True)),
            unit=cast(str | None, data.get("unit")),
            message=cast(str | None, data.get("message")),
        )


@dataclass(slots=True)
class TrustDegradationEvent:
    stage: str
    code: str
    message: str
    severity: TrustSeverity = "warning"
    fallback_used: str | None = None
    evidence: list[TrustEvidenceMetric] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "evidence": [metric.to_dict() for metric in self.evidence],
        }
        if self.fallback_used is not None:
            data["fallback_used"] = self.fallback_used
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrustDegradationEvent:
        severity = data.get("severity", "warning")
        if severity not in SEVERITY_RANK:
            severity = "warning"
        return cls(
            stage=str(data["stage"]),
            code=str(data["code"]),
            message=str(data["message"]),
            severity=cast(TrustSeverity, severity),
            fallback_used=cast(str | None, data.get("fallback_used")),
            evidence=[
                TrustEvidenceMetric.from_dict(metric)
                for metric in data.get("evidence", [])
                if isinstance(metric, dict)
            ],
        )


@dataclass(slots=True)
class TrustStageOutcome:
    stage: str
    status: StageStatus
    message: str | None = None
    evidence: list[TrustEvidenceMetric] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "stage": self.stage,
            "status": self.status,
            "evidence": [metric.to_dict() for metric in self.evidence],
        }
        if self.message is not None:
            data["message"] = self.message
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrustStageOutcome:
        status = data.get("status", "unavailable")
        if status not in {"pending", "success", "degraded", "failed", "skipped", "unavailable"}:
            status = "unavailable"
        return cls(
            stage=str(data["stage"]),
            status=cast(StageStatus, status),
            message=cast(str | None, data.get("message")),
            evidence=[
                TrustEvidenceMetric.from_dict(metric)
                for metric in data.get("evidence", [])
                if isinstance(metric, dict)
            ],
        )


@dataclass(slots=True)
class TrustSummary:
    status: TrustStatus
    degradation_count: int = 0
    highest_severity: TrustSeverity | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "degradation_count": self.degradation_count,
            "highest_severity": self.highest_severity,
        }


@dataclass(slots=True)
class DraftTrustReport:
    status: TrustStatus
    stage_outcomes: list[TrustStageOutcome] = field(default_factory=list)
    degradation_events: list[TrustDegradationEvent] = field(default_factory=list)
    failing_stage: str | None = None
    error_message: str | None = None
    schema_version: int = 1
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def summary(self) -> TrustSummary:
        highest_severity: TrustSeverity | None = None
        for event in self.degradation_events:
            if (
                highest_severity is None
                or SEVERITY_RANK[event.severity] > SEVERITY_RANK[highest_severity]
            ):
                highest_severity = event.severity
        return TrustSummary(
            status=self.status,
            degradation_count=len(self.degradation_events),
            highest_severity=highest_severity,
        )

    def add_stage(
        self,
        stage: str,
        status: StageStatus,
        *,
        message: str | None = None,
        evidence: list[TrustEvidenceMetric] | None = None,
    ) -> None:
        self.stage_outcomes.append(
            TrustStageOutcome(
                stage=stage,
                status=status,
                message=message,
                evidence=evidence or [],
            )
        )

    def set_stage(
        self,
        stage: str,
        status: StageStatus,
        *,
        message: str | None = None,
        evidence: list[TrustEvidenceMetric] | None = None,
    ) -> None:
        outcome = TrustStageOutcome(
            stage=stage,
            status=status,
            message=message,
            evidence=evidence or [],
        )
        self.stage_outcomes = [
            existing for existing in self.stage_outcomes if existing.stage != stage
        ]
        self.stage_outcomes.append(outcome)

    def add_degradation(
        self,
        stage: str,
        code: str,
        message: str,
        *,
        severity: TrustSeverity = "warning",
        fallback_used: str | None = None,
        evidence: list[TrustEvidenceMetric] | None = None,
    ) -> None:
        self.degradation_events.append(
            TrustDegradationEvent(
                stage=stage,
                code=code,
                message=message,
                severity=severity,
                fallback_used=fallback_used,
                evidence=evidence or [],
            )
        )
        if self.status == "planned":
            self.status = "degraded"

    def mark_failed(self, stage: str, message: str) -> None:
        self.status = "failed"
        self.failing_stage = stage
        self.error_message = message
        self.add_stage(stage, "failed", message=message)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "status": self.status,
            "stage_outcomes": [stage.to_dict() for stage in self.stage_outcomes],
            "degradation_events": [event.to_dict() for event in self.degradation_events],
        }
        if self.failing_stage is not None:
            data["failing_stage"] = self.failing_stage
        if self.error_message is not None:
            data["error_message"] = self.error_message
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DraftTrustReport:
        status = data.get("status", "unknown")
        if status not in {"planned", "degraded", "failed", "unknown"}:
            status = "unknown"
        return cls(
            status=cast(TrustStatus, status),
            stage_outcomes=[
                TrustStageOutcome.from_dict(stage)
                for stage in data.get("stage_outcomes", [])
                if isinstance(stage, dict)
            ],
            degradation_events=[
                TrustDegradationEvent.from_dict(event)
                for event in data.get("degradation_events", [])
                if isinstance(event, dict)
            ],
            failing_stage=cast(str | None, data.get("failing_stage")),
            error_message=cast(str | None, data.get("error_message")),
            schema_version=int(data.get("schema_version", 1)),
            generated_at=str(data.get("generated_at") or datetime.now(UTC).isoformat()),
        )


def new_trust_report() -> DraftTrustReport:
    return DraftTrustReport(status="planned")


def unknown_trust_summary() -> TrustSummary:
    return TrustSummary(status="unknown")


def trust_report_from_json(data: Any) -> DraftTrustReport | None:
    if not isinstance(data, dict):
        return None
    return DraftTrustReport.from_dict(data)


def trust_summary_from_json(data: Any) -> TrustSummary:
    report = trust_report_from_json(data)
    if report is None:
        return unknown_trust_summary()
    return report.summary
