"""Alert suppression — one open card speaks for a service on a lane.

Per-event dedupe already stopped the same signal from minting a second
card. These tests cover the operator-workload half: a service that
deviates again the next day folds into the card still sitting unanswered
in the inbox, as a counted repeat rather than a competing card.

The scope rules are the interesting part and each has a test here: a
rejected card does not silence anything, a different lane never
suppresses across (a fraud hold and a cost card on the same service are
two conversations), and the window is operator-tunable — zero disables
the feature outright.
"""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import actions, db
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def seed_analyzed_event(
    *,
    service: str = "compute",
    occurred_on: str = "2026-07-11",
    kind: str = "cost_anomaly",
    z_score: float = 3.5,
) -> int:
    """One analyzed cost event, ready for the recommender chain."""
    payload = {
        "service": service,
        "date": occurred_on,
        "cost": 512.0,
        "service_mean": 128.0,
        "z_score": z_score,
        "severity": "critical" if abs(z_score) >= 3 else "warning",
    }
    envelope = {
        "report": {
            "triage": "REAL",
            "summary": "spend rose sharply",
            "probable_cause": "unverified capacity change",
            "evidence_ids": ["E1"],
            "confidence": {"score": 0.8, "rationale": "clean history"},
        },
        "source": "fake",
        "model": "fake",
        "reflected": False,
    }
    conn = db.connect()
    try:
        with db.writing(conn):
            event_id = db.upsert_event(
                conn,
                kind=kind,
                service=service,
                occurred_on=occurred_on,
                payload_json=json.dumps(payload),
            )
            conn.execute(
                "UPDATE events SET analysis_json = ? WHERE id = ?",
                (json.dumps(envelope), event_id),
            )
        return event_id
    finally:
        conn.close()


def action_count() -> int:
    conn = db.connect()
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM actions").fetchone()["n"]
    finally:
        conn.close()


def set_state(action_id: int, state: str) -> None:
    conn = db.connect()
    try:
        with db.writing(conn):
            conn.execute(
                "UPDATE actions SET state = ? WHERE id = ?", (state, action_id)
            )
    finally:
        conn.close()


# --- the window knob ---------------------------------------------------------


def test_window_defaults_and_survives_garbage(monkeypatch):
    assert actions.suppression_window_hours() == 24.0
    monkeypatch.setenv("SENTINEL_SUPPRESSION_WINDOW_HOURS", "not-a-number")
    assert actions.suppression_window_hours() == 24.0
    # nan/inf parse as floats but would break the SQLite datetime modifier
    monkeypatch.setenv("SENTINEL_SUPPRESSION_WINDOW_HOURS", "nan")
    assert actions.suppression_window_hours() == 24.0
    monkeypatch.setenv("SENTINEL_SUPPRESSION_WINDOW_HOURS", "6")
    assert actions.suppression_window_hours() == 6.0


def test_disabled_window_finds_no_suppressor(client, monkeypatch):
    first = seed_analyzed_event(occurred_on="2026-07-11")
    client.post(f"/anomalies/{first}/recommend")
    monkeypatch.setenv("SENTINEL_SUPPRESSION_WINDOW_HOURS", "0")

    second = seed_analyzed_event(occurred_on="2026-07-12")
    body = client.post(f"/anomalies/{second}/recommend").json()

    assert body["suppressed"] is False
    assert action_count() == 2


# --- the fold ----------------------------------------------------------------


def test_repeat_on_the_same_service_folds_into_the_open_card(client):
    first = seed_analyzed_event(occurred_on="2026-07-11")
    opened = client.post(f"/anomalies/{first}/recommend").json()
    assert opened["suppressed"] is False

    second = seed_analyzed_event(occurred_on="2026-07-12", z_score=4.1)
    folded = client.post(f"/anomalies/{second}/recommend").json()

    # the repeat answers with the card that already speaks for the service
    assert folded["suppressed"] is True
    assert folded["reused"] is True
    assert folded["action_id"] == opened["action_id"]
    assert folded["event_id"] == second
    assert folded["suppressed_count"] == 1
    # ...and no second card reached the inbox
    assert action_count() == 1


def test_the_fold_is_visible_on_the_card_and_its_trail(client):
    first = seed_analyzed_event(occurred_on="2026-07-11")
    action_id = client.post(f"/anomalies/{first}/recommend").json()["action_id"]
    for day, z in (("2026-07-12", 4.1), ("2026-07-13", 2.9)):
        second = seed_analyzed_event(occurred_on=day, z_score=z)
        client.post(f"/anomalies/{second}/recommend")

    card = next(
        item
        for item in client.get("/actions").json()["actions"]
        if item["id"] == action_id
    )
    assert card["suppressed_count"] == 2
    block = card["detail"]["suppression"]
    assert block["window_hours"] == 24.0
    assert [repeat["date"] for repeat in block["repeats"]] == [
        "2026-07-12",
        "2026-07-13",
    ]
    assert [repeat["z_score"] for repeat in block["repeats"]] == [4.1, 2.9]
    # the append-only trail carries every fold, actor included
    folds = [step for step in card["history"] if step["transition"] == "suppressed"]
    assert len(folds) == 2
    assert folds[0]["actor"] == "system:suppression"
    assert "2026-07-12" in folds[0]["note"]


def test_suppression_spends_no_llm_quota(client):
    """The fold happens before the chain runs — the repeat costs nothing."""
    first = seed_analyzed_event(occurred_on="2026-07-11")
    client.post(f"/anomalies/{first}/recommend")
    conn = db.connect()
    try:
        before = conn.execute("SELECT COUNT(*) AS n FROM ai_usage").fetchone()["n"]
    finally:
        conn.close()

    second = seed_analyzed_event(occurred_on="2026-07-12")
    assert client.post(f"/anomalies/{second}/recommend").json()["suppressed"] is True

    conn = db.connect()
    try:
        after = conn.execute("SELECT COUNT(*) AS n FROM ai_usage").fetchone()["n"]
    finally:
        conn.close()
    assert after == before


# --- the scope rules ---------------------------------------------------------


def test_a_different_service_opens_its_own_card(client):
    first = seed_analyzed_event(service="compute", occurred_on="2026-07-11")
    client.post(f"/anomalies/{first}/recommend")

    second = seed_analyzed_event(service="database", occurred_on="2026-07-12")
    body = client.post(f"/anomalies/{second}/recommend").json()

    assert body["suppressed"] is False
    assert action_count() == 2


def test_a_rejected_card_silences_nothing(client):
    first = seed_analyzed_event(occurred_on="2026-07-11")
    action_id = client.post(f"/anomalies/{first}/recommend").json()["action_id"]
    set_state(action_id, "rejected")

    second = seed_analyzed_event(occurred_on="2026-07-12")
    body = client.post(f"/anomalies/{second}/recommend").json()

    # the operator said no to that card; a fresh signal deserves its own
    assert body["suppressed"] is False
    assert action_count() == 2


def test_an_executed_card_silences_nothing(client):
    first = seed_analyzed_event(occurred_on="2026-07-11")
    action_id = client.post(f"/anomalies/{first}/recommend").json()["action_id"]
    set_state(action_id, "executed")

    second = seed_analyzed_event(occurred_on="2026-07-12")
    assert client.post(f"/anomalies/{second}/recommend").json()["suppressed"] is False
    assert action_count() == 2


def test_an_approved_card_silences_nothing(client):
    first = seed_analyzed_event(occurred_on="2026-07-11")
    action_id = client.post(f"/anomalies/{first}/recommend").json()["action_id"]
    set_state(action_id, "approved")

    second = seed_analyzed_event(occurred_on="2026-07-12")
    body = client.post(f"/anomalies/{second}/recommend").json()

    # the hand has answered that card; folding a NEW fact into it would
    # quietly apply an old verdict to a signal nobody has judged
    assert body["suppressed"] is False
    assert action_count() == 2


def test_suppression_never_crosses_lanes(client):
    """A fraud hold and a cost card on one service are two conversations."""
    conn = db.connect()
    try:
        with db.writing(conn):
            fraud_event = db.upsert_event(
                conn,
                kind="fraud_signal",
                service="compute",
                occurred_on="2026-07-11",
                payload_json="{}",
            )
            conn.execute(
                "INSERT INTO actions (event_id, title, detail_json) VALUES (?, ?, ?)",
                (fraud_event, "hold suggested", json.dumps({"kind": "fraud_hold"})),
            )
    finally:
        conn.close()

    cost_event = seed_analyzed_event(service="compute", occurred_on="2026-07-12")
    body = client.post(f"/anomalies/{cost_event}/recommend").json()

    assert body["suppressed"] is False
    assert action_count() == 2


def test_find_suppressor_ignores_the_signals_own_card(client):
    """Re-recommending one event replays its card; it never suppresses itself."""
    event_id = seed_analyzed_event(occurred_on="2026-07-11")
    opened = client.post(f"/anomalies/{event_id}/recommend").json()
    replay = client.post(f"/anomalies/{event_id}/recommend").json()

    assert replay["action_id"] == opened["action_id"]
    assert replay["reused"] is True
    assert replay["suppressed"] is False
    assert replay["suppressed_count"] == 0


# --- defensive ---------------------------------------------------------------


def test_a_stale_database_is_widened_to_accept_the_new_transition(tmp_path):
    """A dev DB predating 'suppressed' must not reject it forever."""
    path = tmp_path / "stale.db"
    db.init_db(path)
    # rewind action_events to the constraint it shipped with before
    # suppression existed, keeping a row on the old trail
    conn = db.connect(path)
    try:
        with db.writing(conn):
            conn.execute(
                "INSERT INTO events (kind, service, occurred_on, payload_json) "
                "VALUES ('cost_anomaly', 'compute', '2026-07-11', '{}')"
            )
            conn.execute(
                "INSERT INTO actions (id, event_id, title, detail_json) "
                "VALUES (1, 1, 'legacy card', '{}')"
            )
            conn.execute("DROP TABLE action_events")
            conn.execute(
                "CREATE TABLE action_events ("
                "  id INTEGER PRIMARY KEY,"
                "  action_id INTEGER NOT NULL REFERENCES actions(id),"
                "  transition TEXT NOT NULL CHECK (transition IN"
                "    ('filed', 'approved', 'rejected', 'executed', 'reopened',"
                "     'expired')),"
                "  actor TEXT, note TEXT,"
                "  created_at TEXT NOT NULL DEFAULT (datetime('now'))"
                ")"
            )
            conn.execute(
                "INSERT INTO action_events (action_id, transition, actor) "
                "VALUES (1, 'filed', 'agent:recommender')"
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO action_events (action_id, transition) "
                    "VALUES (1, 'suppressed')"
                )
    finally:
        conn.close()

    db.init_db(path)

    conn = db.connect(path)
    try:
        with db.writing(conn):
            conn.execute(
                "INSERT INTO action_events (action_id, transition) "
                "VALUES (1, 'suppressed')"
            )
        rows = conn.execute(
            "SELECT transition FROM action_events ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    # the pre-existing trail survived the rebuild
    assert [row["transition"] for row in rows] == ["filed", "suppressed"]


def test_a_card_with_corrupt_detail_still_absorbs_the_repeat(client):
    first = seed_analyzed_event(occurred_on="2026-07-11")
    action_id = client.post(f"/anomalies/{first}/recommend").json()["action_id"]
    conn = db.connect()
    try:
        with db.writing(conn):
            conn.execute(
                "UPDATE actions SET detail_json = ? WHERE id = ?",
                ('"not an object"', action_id),
            )
            row = conn.execute(
                "SELECT * FROM actions WHERE id = ?", (action_id,)
            ).fetchone()
            assert actions.record_suppression(conn, row, occurred_on="2026-07-12") == 1
        detail = json.loads(
            conn.execute(
                "SELECT detail_json FROM actions WHERE id = ?", (action_id,)
            ).fetchone()["detail_json"]
        )
    finally:
        conn.close()
    assert detail["suppression"]["suppressed_count"] == 1
