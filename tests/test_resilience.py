"""Bad days, injected on purpose — the chain under failure, not the units.

The unit-level failure paths already have homes: ``test_feeds.py`` pins the
last-good payload and the dropped records, ``test_llm.py`` pins the retry
budget and the fallback tag, ``test_dispatch.py`` pins that a replayed
execute never re-fires the webhook, ``test_demo_ops.py`` pins the 503
envelope. What none of them ask is the question an operator actually has on
a bad day: **with that part broken, does the product still work?**

So each case here breaks one thing and then drives the real chain over HTTP:

- the cost feed collapses mid-scan → the estate still scans, on the last
  good payload, and /health says which lane fell back instead of claiming
  live data over stale panels;
- a feed answers with garbage → the malformed rows are dropped, the good
  ones survive, and every figure downstream stays finite (a NaN in one row
  would poison every mean and z-score after it);
- the provider is unavailable for the whole pulse → proposals are still
  filed, from the rule-based fallback, and no request 500s;
- two operators write at once → both verdicts land; SQLite's busy timeout
  makes the loser wait, not lose;
- an execute is replayed with the same Idempotency-Key → the audit trail
  gains nothing the second time: one executed row, one bus line.

No network anywhere: the provider is faked, the feed loader's single HTTP
seam is monkeypatched, and the contention is two real threads on one
temporary database.
"""

import json
import math
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app import db, feeds
from app.llm import FakeProvider, LLMUnavailableError
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


class DeadProvider(FakeProvider):
    """Every call fails the way an exhausted quota or a timeout fails."""

    def generate(self, prompt, **kwargs):
        raise LLMUnavailableError("read timeout after 30s")

    @property
    def model(self):
        return "gemini-2.5-flash"


# A live cost feed with a shape the app accepts. The detector needs
# MIN_HISTORY records per service before it will score anything, so the
# calm run is twelve days long; day thirteen is the obvious spike.
GOOD_FEED = {
    "currency": "EUR",
    "daily_costs": [
        {"date": f"2026-07-{day:02d}", "service": service, "cost": cost}
        for day, service, cost in (
            [(day, "edge", 10.0 + (day % 3) * 0.2) for day in range(1, 13)]
            + [(day, "queue", 20.0 + (day % 4) * 0.3) for day in range(1, 13)]
            + [(13, "edge", 61.0), (13, "queue", 20.1)]
        )
    ],
}


def _serving(client, lane: str) -> str:
    return client.get("/health").json()["data_sources"][lane]


# --- a feed dies mid-scan ----------------------------------------------------


def test_the_estate_still_scans_when_the_feed_collapses(client, monkeypatch):
    """Stale-but-good beats dark panels, and the badge admits which it is."""
    monkeypatch.setenv("SENTINEL_COSTS_FEED_URL", "https://feed.example.test/costs")
    monkeypatch.setenv("SENTINEL_FEED_TTL_SECONDS", "0")  # every read refetches
    monkeypatch.setattr(feeds, "_get_json", lambda url: GOOD_FEED)

    live = client.get("/anomalies")
    assert live.status_code == 200
    live_services = {row["service"] for row in live.json()["anomalies"]}
    assert live_services, "the seeded spike must register while the feed is up"
    assert _serving(client, "costs") == "feed"

    def collapse(url):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(feeds, "_get_json", collapse)

    after = client.get("/anomalies")
    assert after.status_code == 200, "a dead feed must not take the scan down"
    # The same estate, from the last good payload — not a different one.
    assert {row["service"] for row in after.json()["anomalies"]} == live_services
    assert client.get("/costs/summary").status_code == 200

    # With no payload to fall back on, the bundled fixture answers — and the
    # badge stops claiming live data over it.
    feeds.reset_cache()
    cold = client.get("/anomalies")
    assert cold.status_code == 200
    assert _serving(client, "costs") == feeds.MOCK_FALLBACK


def test_a_dead_feed_does_not_disturb_the_fixture_it_falls_back_to(client, monkeypatch):
    """The fallback estate is the shipped one, byte for byte."""
    baseline = client.get("/costs/summary").json()

    monkeypatch.setenv("SENTINEL_COSTS_FEED_URL", "https://feed.example.test/costs")
    monkeypatch.setattr(feeds, "_get_json", lambda url: (_ for _ in ()).throw(
        RuntimeError("feed down")
    ))
    feeds.reset_cache()

    assert client.get("/costs/summary").json() == baseline


# --- a feed answers with garbage ---------------------------------------------


def test_malformed_rows_are_dropped_and_never_reach_the_detector(client, monkeypatch):
    """A NaN in one row would poison every mean and z-score after it."""
    poisoned = {
        "currency": "EUR",
        "daily_costs": GOOD_FEED["daily_costs"]
        + [
            {"date": "2026-07-20", "service": "edge", "cost": float("nan")},
            {"date": "2026-07-20", "service": "edge", "cost": float("inf")},
            {"date": "not-a-date", "service": "edge", "cost": 5.0},
            {"date": "2026-07-20", "service": "", "cost": 5.0},
            {"date": "2026-07-20", "service": "edge", "cost": "twelve"},
            "not even a record",
        ],
    }
    monkeypatch.setenv("SENTINEL_COSTS_FEED_URL", "https://feed.example.test/costs")
    monkeypatch.setattr(feeds, "_get_json", lambda url: poisoned)

    summary = client.get("/costs/summary")
    assert summary.status_code == 200
    figures = summary.json()
    assert math.isfinite(figures["total_cost"])

    scan = client.get("/anomalies")
    assert scan.status_code == 200
    for row in scan.json()["anomalies"]:
        assert math.isfinite(row["z_score"]), "a poisoned row reached the detector"
        assert math.isfinite(row["service_mean"])
        assert math.isfinite(row["cost"])

    # The good rows are all still there: dropping the bad ones is not
    # dropping the day they landed on.
    daily = client.get("/costs/daily").json()
    assert "2026-07-13" in daily["dates"]
    assert all(math.isfinite(total) for total in daily["totals"])


def test_a_feed_of_pure_garbage_falls_back_instead_of_serving_nothing(
    client, monkeypatch
):
    monkeypatch.setenv("SENTINEL_COSTS_FEED_URL", "https://feed.example.test/costs")
    monkeypatch.setattr(feeds, "_get_json", lambda url: {"daily_costs": ["junk", 42]})
    feeds.reset_cache()

    response = client.get("/anomalies")
    assert response.status_code == 200
    assert _serving(client, "costs") == feeds.MOCK_FALLBACK


# --- the provider is gone ----------------------------------------------------


def test_the_pulse_still_files_proposals_with_no_provider(client, monkeypatch):
    """The demo's whole reliability claim: the chain degrades, it does not stop."""
    for module in ("analyst", "recommender", "chronicler"):
        monkeypatch.setattr(f"app.{module}.get_provider", lambda: DeadProvider())

    response = client.post("/pulse")
    assert response.status_code == 200, "a dead provider must not 500 the chain"
    report = response.json()
    assert report["signals"] > 0, "detection is deterministic and never needed the LLM"
    assert report["proposals_filed"] > 0, "the operator still gets cards to decide"

    inbox = client.get("/actions").json()["actions"]
    assert inbox, "the decision desk is populated from the rule-based path"


def test_analyze_and_recommend_answer_from_the_rules_when_the_provider_dies(
    client, monkeypatch
):
    """Every money figure is Python arithmetic — it never needed a model."""
    client.post("/pulse")
    event_id = client.get("/anomalies").json()["anomalies"][0]["id"]

    for module in ("analyst", "recommender"):
        monkeypatch.setattr(f"app.{module}.get_provider", lambda: DeadProvider())

    analysis = client.post(f"/anomalies/{event_id}/analyze")
    assert analysis.status_code == 200
    assert analysis.json()["source"] == "fallback"

    recommendation = client.post(f"/anomalies/{event_id}/recommend")
    assert recommendation.status_code == 200
    body = recommendation.json()
    assert body["options"], "the operator is still offered something to decide on"
    # The savings are computed, so they survive the provider that is gone.
    assert math.isfinite(body["savings"]["cautious_monthly"])


# --- two writers at once -----------------------------------------------------


def test_a_contended_write_waits_instead_of_being_lost(client):
    """SQLite's busy timeout makes the loser wait; nothing is dropped.

    Two threads open their own connections — the production shape, since
    connections are per-use — and write at the same moment. Both rows must
    be there afterwards.
    """
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def insert(marker: str) -> None:
        conn = db.connect_ready()
        try:
            barrier.wait(timeout=10)
            with db.writing(conn):
                conn.execute(
                    "INSERT INTO decisions "
                    "(action_id, service, verdict, rationale, input_context_json) "
                    "VALUES (NULL, ?, 'approved', 'contended write', '{}')",
                    (marker,),
                )
        except Exception as error:  # recorded, not raised out of the thread
            errors.append(error)
        finally:
            conn.close()

    threads = [threading.Thread(target=insert, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors, f"a contended write failed instead of waiting: {errors}"
    conn = db.connect_ready()
    try:
        services = {
            row["service"]
            for row in conn.execute(
                "SELECT service FROM decisions WHERE rationale = 'contended write'"
            )
        }
    finally:
        conn.close()
    assert services == {"a", "b"}, "a write was lost under contention"


def test_an_exhausted_busy_timeout_answers_503_not_a_traceback(client, monkeypatch):
    """The one thing worse than a busy database is a stack trace about it."""

    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "connect_ready", busy)
    response = client.get("/actions")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "2"
    assert "retry" in response.json()["detail"].lower()
    assert "Traceback" not in response.text


# --- the same execute, twice -------------------------------------------------


def test_a_replayed_execute_adds_nothing_to_the_audit_trail(client):
    """Idempotency is not only about the answer — the record must not double.

    test_dispatch.py pins that the webhook does not re-fire. This pins the
    other half: the ledger a juror reads gains no phantom second execution.
    """
    client.post("/pulse")
    action_id = client.get("/actions").json()["actions"][0]["id"]
    client.post(f"/actions/{action_id}/approve", json={"rationale": "go"})

    headers = {"Idempotency-Key": "resilience-replay-1"}
    first = client.post(f"/actions/{action_id}/execute", headers=headers)
    assert first.status_code == 200
    assert first.json()["state"] == "executed"

    second = client.post(f"/actions/{action_id}/execute", headers=headers)
    assert second.status_code == 200
    assert second.json() == first.json(), "the replay must echo the stored answer"

    conn = db.connect_ready()
    try:
        executed = conn.execute(
            "SELECT COUNT(*) AS n FROM action_events "
            "WHERE action_id = ? AND transition = 'executed'",
            (action_id,),
        ).fetchone()["n"]
        announced = conn.execute(
            "SELECT COUNT(*) AS n FROM agent_feed WHERE kind = 'execute'"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert executed == 1, "the replay wrote a second execution into the trail"
    assert announced == 1, "the replay announced itself twice on the agent feed"


def test_execute_without_a_key_still_refuses_a_second_run(client):
    """The state machine is the floor: no key, no replay, still no double-run."""
    client.post("/pulse")
    action_id = client.get("/actions").json()["actions"][0]["id"]
    client.post(f"/actions/{action_id}/approve", json={"rationale": "go"})

    assert client.post(f"/actions/{action_id}/execute").status_code == 200
    again = client.post(f"/actions/{action_id}/execute")
    assert again.status_code == 409
    assert "executed" in json.dumps(again.json())
