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
    # Deploy environment (SENTINEL_ENV) — the dashboard shows a LIVE banner
    # when this is "render"; defaults to "local".
    env: str = "local"
    version: str = "0.0.0"
    # Which completion backend answers right now (fake | gemini) — cheap
    # env inspection, no client is instantiated for a liveness ping.
    provider: str = "fake"
    # SENTINEL_READONLY=1 turns the public link into a safe showcase:
    # every POST answers 403 and the dashboard says so.
    readonly: bool = False


class ReadinessCheck(BaseModel):
    """One dependency probe in the readiness report."""

    name: str
    ok: bool
    detail: str


class ReadinessStatus(BaseModel):
    """Readiness (vs. liveness): are the things a request needs present?

    /health says the process is up; /ready verifies the database is
    reachable, the mission config parses and the dataset is loadable — the
    dependencies a real request touches — so a deploy or uptime monitor can
    gate on genuine readiness. ``ready`` is false (and the endpoint answers
    503) if any check fails.
    """

    ready: bool
    version: str = "0.0.0"
    provider: str = "fake"
    checks: list[ReadinessCheck]


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
    # The calendar date each cited evidence row falls on, so the dashboard
    # can ring the cited days by DATE rather than by fragile index math.
    cited_dates: list[str] = []
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
    # Orchestration trace: one entry per chain hop (analyst → memory →
    # recommender → skeptic) with source/model/timing — the pipeline's
    # actual execution, observable instead of claimed.
    trace: list[dict] = Field(default_factory=list)
    # How many prior operator verdicts fed the decision_memory prompt slot.
    memory_considered: int = 0
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
    # Hours left before the request-triggered TTL expires this proposal;
    # None for decided actions or a disabled TTL.
    expires_in_hours: float | None = None


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


class DecisionSearchReport(BaseModel):
    """Filtered view over the whole decision ledger, newest first."""

    count: int
    filters: dict
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


class PulseBriefing(BaseModel):
    """Chronicler agent output: the pulse run narrated for the operator."""

    headline: str
    summary: str
    watch_next: str
    source: Literal["gemini", "fake", "fallback"]
    model: str
    from_cache: bool = False


class PulseReport(BaseModel):
    threshold: float
    mission: str | None = None
    reflex_ms: float | None = None
    signals: int
    security_signals: int = 0
    fraud_signals: int = 0
    # Deterministic cross-lane cards this run filed into the inbox.
    fraud_holds_filed: int = 0
    budget_cards_filed: int = 0
    analyzed: int
    proposals_filed: int
    proposals_reused: int
    # Quota guardrail (S3-⑤): how many provider calls this pulse spent
    # against its cap, and whether it ran dry (agents then answer with
    # their rule-based fallbacks instead of failing).
    llm_budget: int = 0
    llm_calls_used: int = 0
    budget_exhausted: bool = False
    briefing: PulseBriefing | None = None
    chain: list[PulseChainLink]


class LastPulseReport(BaseModel):
    """The most recent pulse run, persisted so a reload keeps the story."""

    ran_at: str
    report: PulseReport


class DemoResetReport(BaseModel):
    """Outcome of an env-gated demo reset (rehearsal hygiene, not product)."""

    cleared: list[str]
    seeded_decisions: int
    note: str


class SecuritySignal(BaseModel):
    # Stable event id assigned at persistence; None before it.
    id: int | None = None
    service: str
    date: str
    count: float
    baseline: float
    z_score: float
    severity: Literal["critical", "warning"]
    detector: str


class SecuritySignalReport(BaseModel):
    metric: str
    threshold: float
    mission: str | None
    reflex_ms: float | None
    window_days: int
    signal_count: int
    insufficient_data_services: list[str]
    signals: list[SecuritySignal]


class FraudRuleHit(BaseModel):
    """One published rule that fired, with its exact point contribution."""

    rule: Literal["amount", "velocity", "geography", "account_age"]
    points: int
    detail: str


class FraudSignal(BaseModel):
    id: str
    date: str
    service: str
    amount: float
    score: int
    band: Literal["clear", "review", "hold_suggested"]
    reasons: list[str]
    # Structured audit of the score: every point is attributable to a
    # published rule — the sum of hits IS the score (clamped at 100).
    rule_hits: list[FraudRuleHit] = Field(default_factory=list)


class FraudSignalReport(BaseModel):
    mission: str | None
    note: str
    count: int  # non-clear signals across ALL scored events (filter-stable)
    # Band breakdown over all scored events, regardless of any filter.
    bands: dict[str, int] = Field(default_factory=dict)
    signals: list[FraudSignal]


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
    # How often the skeptic's verdict actually changed the stance — the
    # debate's measurable effect, not just its occurrence.
    debates_overturned: int = 0


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
