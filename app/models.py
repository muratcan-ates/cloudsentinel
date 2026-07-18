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
    # Detector registry: which statistics produced this flag (defaults keep
    # payloads persisted before Sprint 3 parseable).
    detector: str = "zscore"
    detector_params: dict = Field(default_factory=dict)


class AnomalyReport(BaseModel):
    threshold: float
    records_analyzed: int
    anomaly_count: int
    detector: str = "zscore"
    window_days: int = 28
    insufficient_data_services: list[str] = Field(default_factory=list)
    # Reflex registry: which mission ran the scan and how long the
    # deterministic pass actually took (measured, not claimed).
    mission: str | None = None
    reflex_ms: float | None = None
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


class RecommendedOptionOut(BaseModel):
    stance: Literal["CAUTIOUS", "BOLD"]
    title: str
    description: str
    risk: Literal["low", "medium", "high"]
    rollback: str
    estimated_monthly_saving: float


class SavingsReport(BaseModel):
    daily_excess: float
    cautious_monthly: float
    bold_monthly: float
    method: str


class RecommendationResponse(BaseModel):
    event_id: int
    action_id: int
    action_state: ActionState
    category: Literal["RIGHTSIZING", "CONFIG_REVIEW", "LIFECYCLE", "INVESTIGATION"]
    preferred: Literal["CAUTIOUS", "BOLD"]
    options: list[RecommendedOptionOut]
    savings: SavingsReport
    confidence: ConfidenceReport
    escalation_reason: str | None
    transcript: dict | None
    source: Literal["gemini", "fake", "fallback"]
    model: str
    reused: bool
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
    rationale: str | None = Field(
        None,
        max_length=500,
        description="Why the operator decided this way; feeds decision memory.",
    )

    @field_validator("actor")
    @classmethod
    def actor_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("actor must not be blank")
        return stripped

    @field_validator("rationale")
    @classmethod
    def rationale_blank_becomes_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class DecisionRecord(BaseModel):
    id: int
    action_id: int | None
    service: str
    verdict: Literal["approved", "rejected"]
    rationale: str | None
    decided_at: str


class DecisionListReport(BaseModel):
    service: str
    count: int
    decisions: list[DecisionRecord]


class PulseChainLink(BaseModel):
    event_id: int
    service: str
    severity: Literal["critical", "warning"]
    triage: TriageClass
    action_id: int
    action_state: ActionState
    preferred: Literal["CAUTIOUS", "BOLD"]
    reused: bool


class PulseReport(BaseModel):
    threshold: float
    mission: str | None = None
    reflex_ms: float | None = None
    signals: int
    analyzed: int
    proposals_filed: int
    proposals_reused: int
    chain: list[PulseChainLink]


class HitlFunnel(BaseModel):
    signals: int
    analyzed: int
    proposals: int
    pending: int
    approved: int
    rejected: int
    executed: int
    timeout_rejections: int


class DecisionQuality(BaseModel):
    human_decisions: int
    approval_rate: float | None
    avg_decision_hours: float | None
    approved_estimated_monthly_savings: float
    savings_method: str


class AgentTelemetry(BaseModel):
    triage_distribution: dict[str, int]
    avg_confidence: float | None
    requests_total: int
    cache_hits: int
    by_source: dict[str, int]
    by_agent: dict[str, int]
    debates: int


class DecisionAnalyticsReport(BaseModel):
    funnel: HitlFunnel
    quality: DecisionQuality
    telemetry: AgentTelemetry


class DetectionPrecisionRow(BaseModel):
    service: str
    approved: int
    rejected: int
    precision_proxy: float | None


class DetectionPrecisionReport(BaseModel):
    window_days: int
    approved: int
    rejected: int
    decided: int
    precision_proxy: float | None
    method: str
    services: list[DetectionPrecisionRow]


class ServiceTrendRow(BaseModel):
    service: str
    current_window_total: float
    previous_window_total: float
    change: float | None
    change_pct: float | None
    direction: Literal["up", "down", "flat", "insufficient_history"]


class CostTrendReport(BaseModel):
    currency: str
    period: Period
    window_days: int
    current_window_days: int
    previous_window_days: int
    dates: list[str]
    totals: list[float]
    current_window_total: float
    previous_window_total: float
    change: float | None
    change_pct: float | None
    services: list[ServiceTrendRow]
