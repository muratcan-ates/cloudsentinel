"""Pydantic response models for the CloudSentinel API."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ServiceCostSummary(BaseModel):
    service: str
    total_cost: float
    mean_daily_cost: float
    min_daily_cost: float
    max_daily_cost: float
    share_of_total: float


class Period(BaseModel):
    start: str
    end: str


class CostSummaryReport(BaseModel):
    currency: str
    period: Period
    records_analyzed: int
    total_cost: float
    services: list[ServiceCostSummary]


class HealthStatus(BaseModel):
    status: Literal["ok"]


class DailyServiceSeries(BaseModel):
    service: str
    values: list[float]


class DailyCostReport(BaseModel):
    currency: str
    period: Period
    dates: list[str]
    services: list[DailyServiceSeries]
    totals: list[float]


class Anomaly(BaseModel):
    # Stable event id assigned when the scan persists the signal; None only
    # before persistence (inside the detection layer).
    id: int | None = None
    service: str
    date: str
    cost: float
    service_mean: float
    z_score: float
    severity: Literal["critical", "warning"]


class AnomalyReport(BaseModel):
    threshold: float
    records_analyzed: int
    anomaly_count: int
    anomalies: list[Anomaly]


ActionState = Literal["proposed", "approved", "rejected", "executed"]

TriageClass = Literal["REAL", "SEASONAL", "DATA_ERROR", "KNOWN_CHANGE"]


class ConfidenceReport(BaseModel):
    score: float
    rationale: str


class AnalysisResponse(BaseModel):
    event_id: int
    triage: TriageClass
    summary: str
    probable_cause: str
    evidence_ids: list[str]
    confidence: ConfidenceReport
    source: Literal["gemini", "fake", "fallback"]
    model: str
    reflected: bool
    from_cache: bool


class ActionRecord(BaseModel):
    id: int
    event_id: int | None
    title: str
    detail: dict
    state: ActionState
    proposed_at: str
    decided_at: str | None
    decided_by: str | None
    executed_at: str | None


class ActionListReport(BaseModel):
    count: int
    actions: list[ActionRecord]


class ActionDecisionRequest(BaseModel):
    actor: str = Field(
        "operator",
        min_length=1,
        max_length=80,
        description="Who is taking the decision; recorded in the audit trail.",
    )

    @field_validator("actor")
    @classmethod
    def actor_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("actor must not be blank")
        return stripped
