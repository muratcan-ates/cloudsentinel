"""Pydantic response models for the CloudSentinel API."""

from typing import Literal

from pydantic import BaseModel


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


class Anomaly(BaseModel):
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
