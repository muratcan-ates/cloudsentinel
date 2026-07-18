"""Fraud mission (Sprint 3, S3-②) — deliberately minimal by plan.

A YAML mission (configs/fraud.yaml), a mock event feed and a SIMPLE
deterministic rule score — nothing more in the competition window. Every
figure is arithmetic over the published rules below; every non-clear
signal is a SUGGESTION for operator review. Nothing here (or anywhere)
blocks a payment automatically — human-in-the-loop is the product's
spine, and the fraud lane inherits it unchanged.

Scoring rules (published, reproducible by hand):

- amount vs the account's typical amount: ratio >= 10 -> +40,
  >= 3 -> +25, >= 1.5 -> +10
- velocity: >= 5 transactions in 10 minutes -> +25, >= 3 -> +15
- geography: transaction country differs from home country -> +20
- account age: younger than 30 days -> +15

Total clamps to 100. Bands: >= 70 hold_suggested, >= 40 review,
otherwise clear.
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter

from app.missions import MissionError, get_mission
from app.models import FraudSignal, FraudSignalReport

logger = logging.getLogger("cloudsentinel.fraud")

router = APIRouter(prefix="/fraud", tags=["fraud"])

FRAUD_DATA_FILE = Path(__file__).parent / "data" / "mock_fraud_events.json"

HOLD_BAND = 70
REVIEW_BAND = 40
NEW_ACCOUNT_DAYS = 30

NOTE = (
    "simple deterministic rule score over mock events — every non-clear "
    "signal is a suggestion for operator review; nothing is blocked "
    "automatically (human-in-the-loop)"
)


def load_fraud_dataset() -> dict:
    with FRAUD_DATA_FILE.open() as f:
        return json.load(f)


def simple_score(event: dict) -> tuple[int, list[str]]:
    """Deterministic rule score with the reasons that produced it."""
    score = 0
    reasons: list[str] = []

    typical = float(event.get("typical_amount", 0.0)) or 1.0
    ratio = float(event.get("amount", 0.0)) / typical
    if ratio >= 10:
        score += 40
        reasons.append(f"amount {ratio:.1f}x the account's typical")
    elif ratio >= 3:
        score += 25
        reasons.append(f"amount {ratio:.1f}x the account's typical")
    elif ratio >= 1.5:
        score += 10
        reasons.append(f"amount {ratio:.1f}x the account's typical")

    velocity = int(event.get("tx_last_10m", 0))
    if velocity >= 5:
        score += 25
        reasons.append(f"{velocity} transactions in 10 minutes")
    elif velocity >= 3:
        score += 15
        reasons.append(f"{velocity} transactions in 10 minutes")

    if event.get("country") != event.get("home_country"):
        score += 20
        reasons.append(
            f"transaction from {event.get('country')} against home "
            f"{event.get('home_country')}"
        )

    if int(event.get("account_age_days", 10**6)) < NEW_ACCOUNT_DAYS:
        score += 15
        reasons.append(
            f"account only {event.get('account_age_days')} days old"
        )

    return min(score, 100), reasons


def band_for(score: int) -> str:
    if score >= HOLD_BAND:
        return "hold_suggested"
    if score >= REVIEW_BAND:
        return "review"
    return "clear"


def score_events() -> list[FraudSignal]:
    """Score every mock event; highest score first."""
    dataset = load_fraud_dataset()
    signals = []
    for event in dataset["events"]:
        score, reasons = simple_score(event)
        signals.append(
            FraudSignal(
                id=str(event["id"]),
                date=str(event["date"]),
                service=str(event.get("service", "payments")),
                amount=float(event["amount"]),
                score=score,
                band=band_for(score),
                reasons=reasons,
            )
        )
    signals.sort(key=lambda signal: signal.score, reverse=True)
    return signals


@router.get("/signals")
def get_fraud_signals() -> FraudSignalReport:
    """Score the mock payment feed with the published deterministic rules."""
    try:
        mission_name = get_mission("fraud").mission
    except MissionError:
        logger.warning("fraud mission unavailable; scoring without mission tags")
        mission_name = None
    signals = score_events()
    return FraudSignalReport(
        mission=mission_name,
        note=NOTE,
        count=sum(1 for signal in signals if signal.band != "clear"),
        signals=signals,
    )
