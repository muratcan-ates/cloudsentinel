"""Market watch — standing optimisation opportunities against this estate.

The anomaly lanes answer "what changed?". This lane answers the question a
FinOps operator asks on a quiet day: "what is worth doing anyway?" A curated
catalogue of published market moves (commitment discounts, ARM families,
storage tiering, spot capacity, idle sweeps) is matched against the services
the estate actually runs, and each match is costed against that service's own
run rate.

Three disciplines carry over from the rest of the system:

* **Arithmetic, never generation.** A row's saving band is
  ``service run rate × applies_to_share × published reduction band``. The
  catalogue supplies the band and the assumption; Python does the rest. No
  agent touches this lane.
* **Provenance on every row.** Each opportunity ships its source, the date
  the team last checked it, and the assumption it rests on — so a jury (or an
  operator) can argue with the number instead of trusting it.
* **Suggestions, never actions.** Nothing here files an action or touches the
  decision inbox. These are standing opportunities for a human to pick up.

The full table answers "what is on the menu". The **possible suggestions**
shortlist answers the next question — "so what do I do on Monday?" — by
folding the table into a handful of ranked lines, each anchored to the
service where the move is worth the most, each carrying the estate facts
that make it apply and an honest label for how far the figure can be
trusted. That label is a rule over evidence already on the table (the
signal's own risk rating and provenance, the width of its published band,
the depth of this estate's cost history), never a model's opinion, and a
suggestion that cannot earn it says ``needs review`` out loud rather than
dressing a guess as a number. When nothing lands on this estate the
shortlist says so plainly; an empty list is a finding, not a failure.

The catalogue is bundled and deterministic. ``SENTINEL_MARKET_FEED_URL``
points the lane at an external catalogue in the same shape (the feeds.py
pattern: TTL cache, single-flight, fall back to the bundled file), which is
how a live market-tracking source would arrive without changing this module.
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app import feeds
from app.detection import MIN_HISTORY, load_daily_costs, summarize_costs

logger = logging.getLogger("cloudsentinel.market")

router = APIRouter(prefix="/market", tags=["market"])

MARKET_DATA_FILE = Path(__file__).parent / "data" / "market_watch.json"
MARKET_FEED_ENV = "SENTINEL_MARKET_FEED_URL"

DAYS_PER_MONTH = 30  # same convention the recommender uses for monthly figures

# A shortlist an operator can actually work through in one sitting; the full
# costed table is right there for anyone who wants the rest.
MAX_SUGGESTIONS = 5

# A published band whose high end is twice its low end or more is a range,
# not an estimate — the suggestion may still be worth taking, but the figure
# beside it cannot be called firm.
WIDE_BAND_RATIO = 2.0

NEEDS_REVIEW = "needs review"
# Ranking order: a line a human still has to weigh cannot outrank one they
# could start this afternoon, however large its band.
CONFIDENCE_ORDER = {"high": 0, "moderate": 1, NEEDS_REVIEW: 2}


class MarketOpportunity(BaseModel):
    id: str
    headline: str
    category: str
    service: str
    # Monthly band, computed: run rate × share × published reduction band.
    monthly_saving_low: float
    monthly_saving_high: float
    monthly_saving_mid: float
    service_monthly_run_rate: float
    share_of_service_spend: float
    reduction_band: str  # display form of the two fractions below
    reduction_min: float
    reduction_max: float
    effort: str
    risk: str
    horizon: str
    rationale: str
    watch_out: str
    source: str
    checked: str
    basis: str


class MarketSuggestion(BaseModel):
    rank: int
    suggestion: str
    service: str
    # The catalogue entry this line was derived from — a reader can look the
    # claim up instead of taking it on trust.
    signal: str
    signal_headline: str
    why_here: str  # the estate's own facts that make the signal apply
    monthly_saving_low: float
    monthly_saving_high: float
    confidence: Literal["high", "moderate", "needs review"]
    confidence_basis: str
    also_applies_to: list[str]
    evidence: str  # the catalogue's own rationale, quoted not paraphrased
    watch_out: str
    effort: str
    horizon: str
    source: str
    checked: str


class SuggestionShortlist(BaseModel):
    signals_available: int  # catalogue entries checked
    signals_matched: int  # …that touch a service this estate runs
    shortlisted: int
    suggestions: list[MarketSuggestion]
    note: str


class MarketReport(BaseModel):
    source: str  # curated | feed | mock (feed unavailable)
    reviewed: str
    currency: str
    services_matched: int
    opportunity_count: int
    # Gross totals: bands over the same service overlap, so this is an
    # upper bound on a pursuit-everything scenario, not a forecast.
    gross_monthly_low: float
    gross_monthly_high: float
    overlapping_services: list[str]
    opportunities: list[MarketOpportunity]
    possible_suggestions: SuggestionShortlist
    note: str


def _validate_catalogue(data: dict) -> dict:
    """Normalise a catalogue payload; malformed entries are dropped, not fatal.

    Mirrors the feeds.py contract discipline: a live catalogue that answers
    the wrong shape must never poison the table, and a lane that cannot
    produce a single valid row is treated as unavailable.
    """
    rows = data.get("opportunities")
    if not isinstance(rows, list):
        raise ValueError("catalogue has no opportunities list")
    kept: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        services = row.get("applies_to")
        if not isinstance(services, list) or not services:
            continue
        try:
            share = float(row["applies_to_share"])
            low = float(row["reduction_min"])
            high = float(row["reduction_max"])
        except (KeyError, TypeError, ValueError):
            continue
        # Shares and reductions are fractions; a catalogue that ships 35
        # instead of 0.35 would invent savings an order of magnitude wide.
        if not (0 < share <= 1 and 0 <= low <= high <= 1):
            continue
        identifier = str(row.get("id") or "").strip()
        headline = str(row.get("headline") or "").strip()
        if not identifier or not headline:
            continue
        kept.append(
            {
                **row,
                "id": identifier,
                "headline": headline,
                "applies_to": [str(s).strip().lower() for s in services if str(s).strip()],
                "applies_to_share": share,
                "reduction_min": low,
                "reduction_max": high,
            }
        )
    dropped = len(rows) - len(kept)
    if dropped:
        logger.warning("market catalogue: dropped %d malformed entr(y/ies)", dropped)
    if not kept:
        raise ValueError("catalogue has no valid opportunities")
    return {
        "note": str(data.get("note") or ""),
        "reviewed": str(data.get("reviewed") or "unknown"),
        "opportunities": kept,
    }


def catalogue_source() -> str:
    return "feed" if os.environ.get(MARKET_FEED_ENV, "").strip() else "curated"


def load_catalogue() -> tuple[dict, str]:
    """Return (catalogue, source-actually-served).

    A configured feed that cannot answer falls back to the bundled file and
    says so, exactly like the cost and security lanes: the badge must never
    claim a live source the operator is not looking at.
    """
    if catalogue_source() == "feed":
        try:
            payload = feeds.fetch_json_feed(
                "market", os.environ[MARKET_FEED_ENV].strip(), _validate_catalogue
            )
            return payload, "feed"
        except feeds.FeedUnavailable:
            logger.warning("market feed unavailable — serving the curated catalogue")
            with MARKET_DATA_FILE.open() as f:
                return _validate_catalogue(json.load(f)), feeds.MOCK_FALLBACK
    with MARKET_DATA_FILE.open() as f:
        return _validate_catalogue(json.load(f)), "curated"


@dataclass(frozen=True)
class ServiceFacts:
    """What this estate says about one service, for the suggestion side."""

    share_of_tracked_spend: float  # fraction of the estate's whole cost record
    days_of_history: int  # distinct days of data standing behind the run rate


# A service the cost record cannot describe gets the honest default rather
# than a flattering one: no share, no history, and the first ladder rule
# below sends it to "needs review".
UNKNOWN_SERVICE = ServiceFacts(share_of_tracked_spend=0.0, days_of_history=0)


def estate_facts(records: list, summaries: list) -> dict[str, ServiceFacts]:
    """Per-service context the costed table does not already carry.

    An opportunity row echoes the run rate. A suggestion has to say more:
    how large the service is inside this estate, and how many days of data
    the run rate rests on. Both change what the figure is worth, and both
    come from the estate's own cost record rather than from the catalogue.
    """
    days: dict[str, set[str]] = {}
    for record in records:
        service = str(record["service"]).lower()
        days.setdefault(service, set()).add(str(record["date"]))
    return {
        summary.service.lower(): ServiceFacts(
            share_of_tracked_spend=summary.share_of_total,
            days_of_history=len(days.get(summary.service.lower(), set())),
        )
        for summary in summaries
    }


def _confidence(opportunity: MarketOpportunity, facts: ServiceFacts) -> tuple[str, str]:
    """Label one suggestion, and say in one clause what earned the label.

    A ladder over evidence that is already on the table — first match wins,
    and every rung names the thing it looked at: the estate's side (how much
    history the run rate rests on), the signal's own provenance and risk
    rating, and the width of the published band. "High" is reserved for a
    row that clears every rung; anything a human would have to weigh anyway
    is labelled as such instead of being quietly rounded up.
    """
    if facts.days_of_history < MIN_HISTORY:
        return NEEDS_REVIEW, (
            f"only {facts.days_of_history} day(s) of {opportunity.service} cost "
            f"history, under the {MIN_HISTORY} days this system calls a baseline "
            "anywhere else — the run rate beneath this figure is not yet a rate"
        )
    if opportunity.source == "unattributed" or opportunity.checked == "unknown":
        return NEEDS_REVIEW, (
            "the signal carries no source or no last-checked date, so its "
            "published band cannot be argued with — only believed"
        )
    if opportunity.risk == "high":
        return NEEDS_REVIEW, (
            "the catalogue rates this move high risk, and that is a judgement "
            "for a human rather than for an arithmetic rule"
        )
    hedges: list[str] = []
    if opportunity.risk != "low":
        hedges.append(f"{opportunity.risk} risk")
    if opportunity.effort != "low":
        hedges.append(f"{opportunity.effort} effort")
    # Measured on the published fractions, not on the rounded money: a cent
    # of rounding must never be what decides how much an operator trusts a row.
    if opportunity.reduction_min <= 0:
        hedges.append(f"a {opportunity.reduction_band} band that starts at nothing")
    elif opportunity.reduction_max >= opportunity.reduction_min * WIDE_BAND_RATIO:
        hedges.append(
            f"a {opportunity.reduction_band} band whose high end is at least "
            "double its low"
        )
    if hedges:
        return "moderate", (
            f"{', '.join(hedges)} — enough to shortlist, not enough to call the "
            "figure firm"
        )
    return "high", (
        f"low risk, low effort, a {opportunity.reduction_band} band from "
        f"{opportunity.source} checked {opportunity.checked}, and "
        f"{facts.days_of_history} days of {opportunity.service} cost history "
        "behind the run rate"
    )


def derive_suggestions(
    opportunities: list[MarketOpportunity],
    facts: dict[str, ServiceFacts],
    *,
    signals_available: int,
    limit: int = MAX_SUGGESTIONS,
) -> SuggestionShortlist:
    """Fold the costed table into a short, ranked, evidence-bearing shortlist.

    One line per signal, anchored to the service where that signal is worth
    the most: the same move on a second service is the same piece of work, so
    repeating it would pad the list rather than lengthen it — the other
    services ride along in ``also_applies_to``.

    Ranked by trust first and money second. A larger band an operator still
    has to argue about is worth less on a Monday morning than a smaller one
    they can start, and ties break on the signal id so two runs over the same
    estate produce the same list in the same order.
    """
    anchors: dict[str, MarketOpportunity] = {}
    also: dict[str, list[str]] = {}
    # The table arrives biggest-band-first and stably sorted, so the first
    # row for a signal is already its most valuable service.
    for opportunity in opportunities:
        if opportunity.id in anchors:
            also.setdefault(opportunity.id, []).append(opportunity.service)
        else:
            anchors[opportunity.id] = opportunity

    labelled: list[tuple[MarketOpportunity, str, str]] = []
    for opportunity in anchors.values():
        service_facts = facts.get(opportunity.service, UNKNOWN_SERVICE)
        labelled.append((opportunity, *_confidence(opportunity, service_facts)))
    labelled.sort(
        key=lambda row: (
            CONFIDENCE_ORDER[row[1]],
            -row[0].monthly_saving_mid,
            row[0].id,
        )
    )

    suggestions: list[MarketSuggestion] = []
    for rank, (opportunity, confidence, basis) in enumerate(labelled[:limit], start=1):
        siblings = sorted(also.get(opportunity.id, []))
        service_facts = facts.get(opportunity.service, UNKNOWN_SERVICE)
        suggestions.append(
            MarketSuggestion(
                rank=rank,
                suggestion=(
                    f"{opportunity.headline} — {opportunity.service}"
                    + (f" first, then {', '.join(siblings)}." if siblings else ".")
                ),
                service=opportunity.service,
                signal=opportunity.id,
                signal_headline=opportunity.headline,
                why_here=(
                    f"{opportunity.service} runs at "
                    f"{opportunity.service_monthly_run_rate:,.2f}/mo, "
                    f"{round(service_facts.share_of_tracked_spend * 100)}% of this "
                    "estate's tracked spend, and the catalogue puts "
                    f"{round(opportunity.share_of_service_spend * 100)}% of that "
                    f"within reach of this move; {service_facts.days_of_history} "
                    "day(s) of cost history stand behind the run rate."
                ),
                monthly_saving_low=opportunity.monthly_saving_low,
                monthly_saving_high=opportunity.monthly_saving_high,
                confidence=confidence,
                confidence_basis=basis,
                also_applies_to=siblings,
                evidence=opportunity.rationale,
                watch_out=opportunity.watch_out,
                effort=opportunity.effort,
                horizon=opportunity.horizon,
                source=opportunity.source,
                checked=opportunity.checked,
            )
        )

    if suggestions:
        note = (
            f"{len(anchors)} of {signals_available} bundled signal(s) land on a "
            f"service this estate runs; the {len(suggestions)} that survive the "
            "ranking are shortlisted, one line per signal. Confidence is a rule "
            "over the signal's own risk, provenance and band width plus the depth "
            "of this estate's cost record — never a model's opinion — and "
            f"'{NEEDS_REVIEW}' means exactly that. Nothing here files an action."
        )
    else:
        note = (
            f"Nothing to suggest: {signals_available} bundled signal(s) checked, "
            f"{len(anchors)} of them landing on a service this estate runs, and "
            "nothing reaching the shortlist. An empty list is a finding — the "
            "lane will not invent a move to fill the table."
        )
    return SuggestionShortlist(
        signals_available=signals_available,
        signals_matched=len(anchors),
        shortlisted=len(suggestions),
        suggestions=suggestions,
        note=note,
    )


def build_report(min_monthly_saving: float = 0.0) -> MarketReport:
    """Match the catalogue against the estate and cost every hit."""
    records = load_daily_costs()
    summaries = summarize_costs(records)
    run_rates = {
        summary.service.lower(): summary.mean_daily_cost * DAYS_PER_MONTH
        for summary in summaries
    }
    catalogue, served = load_catalogue()

    opportunities: list[MarketOpportunity] = []
    for entry in catalogue["opportunities"]:
        for service in entry["applies_to"]:
            run_rate = run_rates.get(service)
            if run_rate is None:  # the estate does not run this service
                continue
            addressable = run_rate * entry["applies_to_share"]
            low = round(addressable * entry["reduction_min"], 2)
            high = round(addressable * entry["reduction_max"], 2)
            if high < min_monthly_saving:
                continue
            opportunities.append(
                MarketOpportunity(
                    id=entry["id"],
                    headline=entry["headline"],
                    category=str(entry.get("category") or "general"),
                    service=service,
                    monthly_saving_low=low,
                    monthly_saving_high=high,
                    monthly_saving_mid=round((low + high) / 2, 2),
                    service_monthly_run_rate=round(run_rate, 2),
                    share_of_service_spend=entry["applies_to_share"],
                    reduction_band=(
                        f"{round(entry['reduction_min'] * 100)}–"
                        f"{round(entry['reduction_max'] * 100)}%"
                    ),
                    reduction_min=entry["reduction_min"],
                    reduction_max=entry["reduction_max"],
                    effort=str(entry.get("effort") or "unknown"),
                    risk=str(entry.get("risk") or "unknown"),
                    horizon=str(entry.get("horizon") or "unknown"),
                    rationale=str(entry.get("rationale") or ""),
                    watch_out=str(entry.get("watch_out") or ""),
                    source=str(entry.get("source") or "unattributed"),
                    checked=str(entry.get("checked") or "unknown"),
                    basis=(
                        f"{round(run_rate, 2)}/mo run rate × "
                        f"{round(entry['applies_to_share'] * 100)}% addressable × "
                        f"{round(entry['reduction_min'] * 100)}–"
                        f"{round(entry['reduction_max'] * 100)}% published reduction"
                    ),
                )
            )

    # Biggest credible win first; id keeps the order stable between runs.
    opportunities.sort(key=lambda o: (-o.monthly_saving_mid, o.id, o.service))

    per_service: dict[str, int] = {}
    for opportunity in opportunities:
        per_service[opportunity.service] = per_service.get(opportunity.service, 0) + 1
    overlapping = sorted(s for s, count in per_service.items() if count > 1)

    return MarketReport(
        source=served,
        reviewed=catalogue["reviewed"],
        currency="USD",
        services_matched=len(per_service),
        opportunity_count=len(opportunities),
        gross_monthly_low=round(sum(o.monthly_saving_low for o in opportunities), 2),
        gross_monthly_high=round(sum(o.monthly_saving_high for o in opportunities), 2),
        overlapping_services=overlapping,
        opportunities=opportunities,
        # Derived from the rows above and the same cost record they were
        # costed against — the shortlist can never disagree with the table.
        possible_suggestions=derive_suggestions(
            opportunities,
            estate_facts(records, summaries),
            signals_available=len(catalogue["opportunities"]),
        ),
        note=(
            "Standing opportunities, not anomalies: published market bands costed "
            "against this estate's own run rate. Bands over the same service "
            "overlap, so the gross total is an upper bound on pursuing everything "
            "— never a forecast. Nothing here files an action."
        ),
    )


@router.get("/opportunities")
def get_market_opportunities(
    min_monthly_saving: float = Query(
        0.0,
        ge=0,
        allow_inf_nan=False,
        description=(
            "Drop opportunities whose upper band falls below this monthly "
            "figure — the operator's 'don't show me pocket change' knob."
        ),
    ),
) -> MarketReport:
    """Rank standing cost opportunities for the services this estate runs.

    Carries both halves of the room: the full costed table, and the
    ``possible_suggestions`` shortlist derived from it — the same floor
    applies to both, so raising it narrows what the lane will suggest.
    """
    return build_report(min_monthly_saving)
