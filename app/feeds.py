"""Live data feeds — the mock-to-live seam (Sprint 3, live-data trial).

Each data lane keeps its bundled mock file as the default and gains an
opt-in live mode, resolved per request:

    costs     SENTINEL_COSTS_SOURCE=self          the app's own telemetry
              SENTINEL_COSTS_FEED_URL=https://…   external JSON feed
    security  SENTINEL_SECURITY_FEED_URL=…        external JSON feed
    fraud     SENTINEL_FRAUD_FEED_URL=…           external JSON feed

A feed must answer the exact JSON shape of its mock file; records that
do not fit the contract are dropped (and counted in the log) rather
than poisoning the detectors. Fetches ride the existing request-
triggered model — the scan is the ingestion point, no scheduler — via
a TTL cache so the dashboard's 60s polling never turns into a network
storm. Every failure falls back in order: fresh fetch -> last good
payload -> bundled mock file. With no live knob set, the mock path is
byte-identical to before, which keeps the test suite and CI hermetic.
"""

import logging
import math
import os
import threading
import time
from datetime import date

import httpx

logger = logging.getLogger("cloudsentinel.feeds")

COSTS_SOURCE_ENV = "SENTINEL_COSTS_SOURCE"  # self | mock (default)
COSTS_FEED_ENV = "SENTINEL_COSTS_FEED_URL"
SECURITY_FEED_ENV = "SENTINEL_SECURITY_FEED_URL"
FRAUD_FEED_ENV = "SENTINEL_FRAUD_FEED_URL"
FEED_TTL_ENV = "SENTINEL_FEED_TTL_SECONDS"

DEFAULT_TTL_SECONDS = 300.0
FETCH_TIMEOUT_SECONDS = 5.0

# name -> (fetched_at_monotonic, validated_payload). Guarded by _lock;
# the per-name locks in _fetch_locks make refreshes single-flight, so
# sync endpoints racing in the threadpool cannot stampede a cold feed.
_cache: dict[str, tuple[float, dict]] = {}
_fetch_locks: dict[str, threading.Lock] = {}
# lane -> the source that last actually answered ("feed", or the mock
# fallback note) — /health must tell the truth, not repeat the config.
_served: dict[str, str] = {}
_lock = threading.Lock()

MOCK_FALLBACK = "mock (feed unavailable)"


class FeedUnavailable(Exception):
    """The live feed cannot answer and no cached payload exists."""


def feed_ttl_seconds() -> float:
    raw = os.environ.get(FEED_TTL_ENV, "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TTL_SECONDS
    return value if value >= 0 else DEFAULT_TTL_SECONDS


def costs_source() -> str:
    """Resolve the cost lane's source: self | feed | mock.

    An explicit SENTINEL_COSTS_SOURCE=mock wins over a configured feed
    URL, so a demo can flip back to the fixture without unsetting it.
    """
    raw = os.environ.get(COSTS_SOURCE_ENV, "").strip().lower()
    if raw == "self":
        return "self"
    if raw != "mock" and os.environ.get(COSTS_FEED_ENV, "").strip():
        return "feed"
    return "mock"


def security_source() -> str:
    return "feed" if os.environ.get(SECURITY_FEED_ENV, "").strip() else "mock"


def fraud_source() -> str:
    return "feed" if os.environ.get(FRAUD_FEED_ENV, "").strip() else "mock"


def data_sources() -> dict[str, str]:
    """Per-lane source map for /health, the boot manifest and the UI.

    Reports what is actually being served, not just what is configured:
    a lane configured for a feed that last fell back to the fixture
    answers "mock (feed unavailable)" so the dashboard badge cannot
    claim live data over mock panels.
    """
    sources = {
        "costs": costs_source(),
        "security": security_source(),
        "fraud": fraud_source(),
    }
    with _lock:
        for lane, configured in sources.items():
            served = _served.get(lane)
            if configured == "feed" and served and served != "feed":
                sources[lane] = served
    return sources


def record_fallback(name: str) -> None:
    """Loaders report here when a configured feed fell back to the mock."""
    with _lock:
        _served[name] = MOCK_FALLBACK


def reset_cache() -> None:
    """Forget cached payloads and served-source state (tests, demo resets)."""
    with _lock:
        _cache.clear()
        _served.clear()


def _get_json(url: str) -> dict:
    """One HTTP GET, strict timeout. Isolated for tests to monkeypatch."""
    response = httpx.get(url, timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True)
    response.raise_for_status()
    return response.json()


def _fetch(name: str, url: str, validate) -> dict:
    """Single-flight, TTL-cached fetch with the stale-payload fallback.

    Concurrent requests that see an expired entry wait on a per-name
    lock and then return the winner's payload instead of stampeding the
    feed. A failed refresh re-stamps the stale payload, so a down feed
    is retried once per TTL, not on every dashboard poll. Raises
    FeedUnavailable only when the fetch fails AND no previous payload
    exists — the caller then falls back to its mock file.
    """
    ttl = feed_ttl_seconds()
    with _lock:
        cached = _cache.get(name)
        if cached and time.monotonic() - cached[0] < ttl:
            return cached[1]
        fetch_lock = _fetch_locks.setdefault(name, threading.Lock())
    with fetch_lock:
        # Double-check: another thread may have refreshed while we waited.
        with _lock:
            cached = _cache.get(name)
            if cached and time.monotonic() - cached[0] < ttl:
                return cached[1]
        try:
            payload = validate(_get_json(url))
        except Exception as exc:
            if cached:
                logger.warning(
                    "feed %s failed (%s) — serving last good payload", name, exc
                )
                with _lock:
                    _cache[name] = (time.monotonic(), cached[1])
                    _served[name] = "feed"
                return cached[1]
            logger.warning("feed %s failed (%s) — no cached payload", name, exc)
            raise FeedUnavailable(name) from exc
        with _lock:
            _cache[name] = (time.monotonic(), payload)
            _served[name] = "feed"
        return payload


def _iso_or_none(value) -> str | None:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


def _validate_costs(data: dict) -> dict:
    """Normalize a costs feed to the mock_costs.json contract."""
    rows = data.get("daily_costs")
    if not isinstance(rows, list):
        raise ValueError("feed has no daily_costs list")
    kept = 0
    totals: dict[tuple[str, str], float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _iso_or_none(row.get("date"))
        service = str(row.get("service") or "").strip()
        try:
            cost = float(row.get("cost"))
        except (TypeError, ValueError):
            continue
        # json.loads accepts NaN/Infinity literals; either would silently
        # poison every mean and z-score downstream.
        if not (day and service and math.isfinite(cost)):
            continue
        kept += 1
        # Duplicate (service, date) rows are summed — the same semantics
        # build_daily_series applies — so one day maps to one record and
        # the events natural key never sees two rival payloads.
        totals[(day, service)] = totals.get((day, service), 0.0) + cost
    dropped = len(rows) - kept
    if dropped:
        logger.warning("costs feed: dropped %d malformed record(s)", dropped)
    records = [
        {"date": day, "service": service, "cost": cost}
        for (day, service), cost in sorted(totals.items())
    ]
    if not records:
        raise ValueError("feed has no valid cost records")
    dates = [r["date"] for r in records]
    period = data.get("period") or {}
    return {
        "description": str(data.get("description") or "live cost feed"),
        "currency": str(data.get("currency") or "USD"),
        "period": {
            "start": _iso_or_none(period.get("start")) or min(dates),
            "end": _iso_or_none(period.get("end")) or max(dates),
        },
        "daily_costs": records,
    }


def _validate_security(data: dict) -> dict:
    """Normalize a security feed to the mock_security_events.json contract."""
    rows = data.get("daily_counts")
    if not isinstance(rows, list):
        raise ValueError("feed has no daily_counts list")
    kept = 0
    totals: dict[tuple[str, str], int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _iso_or_none(row.get("date"))
        service = str(row.get("service") or "").strip()
        try:
            count = int(row.get("count"))
        except (TypeError, ValueError):
            continue
        if not (day and service and count >= 0):
            continue
        kept += 1
        # One count per service per day — duplicates sum, matching the
        # security lane's one-signal-per-day natural key.
        totals[(day, service)] = totals.get((day, service), 0) + count
    dropped = len(rows) - kept
    if dropped:
        logger.warning("security feed: dropped %d malformed record(s)", dropped)
    records = [
        {"service": service, "date": day, "count": count}
        for (day, service), count in sorted(totals.items())
    ]
    if not records:
        raise ValueError("feed has no valid security records")
    dates = [r["date"] for r in records]
    period = data.get("period") or {}
    return {
        "metric": str(data.get("metric") or "failed_login_count"),
        "period": {
            "start": _iso_or_none(period.get("start")) or min(dates),
            "end": _iso_or_none(period.get("end")) or max(dates),
        },
        "daily_counts": records,
    }


def _validate_fraud(data: dict) -> dict:
    """Normalize a fraud feed to the mock_fraud_events.json contract.

    The enrichment fields (typical_amount, account_age_days, home_country,
    tx_last_10m) are required: the rule score degrades silently without
    them, and a quiet degradation is worse than a rejected feed.
    """
    rows = data.get("events")
    if not isinstance(rows, list):
        raise ValueError("feed has no events list")
    # Keyed by (id, date) — the lane's events natural key — so a feed
    # restating a transaction supersedes it instead of racing it.
    by_key: dict[tuple[str, str], dict] = {}
    kept = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _iso_or_none(row.get("date"))
        tx_id = str(row.get("id") or "").strip()
        country = str(row.get("country") or "").strip()
        home_country = str(row.get("home_country") or "").strip()
        # Coerce every numeric the rule score consumes — presence is not
        # enough: score_breakdown float()/int()s these, and one "n/a"
        # would 500 every scan while sitting cached as last-good.
        try:
            amount = float(row.get("amount"))
            typical_amount = float(row.get("typical_amount"))
            account_age_days = int(row.get("account_age_days"))
            tx_last_10m = int(row.get("tx_last_10m"))
        except (TypeError, ValueError):
            continue
        if not (day and tx_id and country and home_country):
            continue
        if not (math.isfinite(amount) and math.isfinite(typical_amount)):
            continue
        kept += 1
        by_key[(tx_id, day)] = {
            **row,
            "id": tx_id,
            "date": day,
            "amount": amount,
            "typical_amount": typical_amount,
            "account_age_days": account_age_days,
            "tx_last_10m": tx_last_10m,
            "country": country,
            "home_country": home_country,
        }
    dropped = len(rows) - kept
    if dropped:
        logger.warning("fraud feed: dropped %d malformed record(s)", dropped)
    records = list(by_key.values())
    if not records:
        raise ValueError("feed has no valid fraud events")
    dates = [r["date"] for r in records]
    period = data.get("period") or {}
    return {
        "currency": str(data.get("currency") or "USD"),
        "period": {
            "start": _iso_or_none(period.get("start")) or min(dates),
            "end": _iso_or_none(period.get("end")) or max(dates),
        },
        "events": records,
    }


def fetch_costs_feed() -> dict:
    return _fetch("costs", os.environ[COSTS_FEED_ENV].strip(), _validate_costs)


def fetch_security_feed() -> dict:
    return _fetch(
        "security", os.environ[SECURITY_FEED_ENV].strip(), _validate_security
    )


def fetch_fraud_feed() -> dict:
    return _fetch("fraud", os.environ[FRAUD_FEED_ENV].strip(), _validate_fraud)
