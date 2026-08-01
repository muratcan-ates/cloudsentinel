"""events.evidence_snapshot_json — the frozen E1..En audit window.

The first analysis freezes the evidence it actually read; re-analysis
reuses that exact window, so an event id can never quietly mean different
E1..En after a live feed moves on. The snapshot only falls together with
the analysis when upsert_event sees a re-stated payload.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import db
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _seed_cost_anomaly(service="compute", occurred_on="2026-07-01"):
    payload = {
        "service": service,
        "date": occurred_on,
        "cost": 512.0,
        "service_mean": 128.0,
        "z_score": 3.5,
        "severity": "critical",
    }
    conn = db.connect_ready()
    try:
        with db.writing(conn):
            event_id = db.upsert_event(
                conn,
                kind="cost_anomaly",
                service=service,
                occurred_on=occurred_on,
                payload_json=json.dumps(payload),
            )
    finally:
        conn.close()
    return event_id, payload


def _event_row(event_id):
    conn = db.connect_ready()
    try:
        return conn.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ).fetchone()
    finally:
        conn.close()


def test_first_analysis_freezes_the_evidence_window(client):
    event_id, _ = _seed_cost_anomaly()
    response = client.post(f"/anomalies/{event_id}/analyze")
    assert response.status_code == 200

    row = _event_row(event_id)
    snapshot = json.loads(row["evidence_snapshot_json"])
    assert snapshot, "the analyzed event must carry its frozen evidence window"
    assert all({"eid", "date", "cost"} <= set(entry) for entry in snapshot)

    envelope = json.loads(row["analysis_json"])
    meta = envelope["meta"]
    assert len(meta["prompt_sha256"]) == 64
    assert len(meta["evidence_fingerprint"]) == 64
    assert meta["mission"]
    assert meta["analyzed_at"]


def test_reanalysis_reuses_the_frozen_window(client):
    """Re-running the analyst must read the stored snapshot, not live data."""
    event_id, _ = _seed_cost_anomaly()
    assert client.post(f"/anomalies/{event_id}/analyze").status_code == 200

    # Freeze a synthetic window with unmistakable ids, then force a re-run.
    synthetic = [
        {"eid": f"S{index}", "date": f"1999-01-0{index}", "cost": 10.0 * index}
        for index in (1, 2, 3)
    ]
    conn = db.connect_ready()
    try:
        with db.writing(conn):
            conn.execute(
                "UPDATE events SET evidence_snapshot_json = ?, "
                "analysis_json = NULL WHERE id = ?",
                (json.dumps(synthetic), event_id),
            )
    finally:
        conn.close()

    rerun = client.post(f"/anomalies/{event_id}/analyze").json()
    # the fake analyst cites the last rows of whatever window it was given —
    # S-ids prove the frozen snapshot was the window, not live build_evidence
    assert rerun["evidence_ids"], "the re-run must cite evidence"
    assert all(eid.startswith("S") for eid in rerun["evidence_ids"])

    # and the re-run must not overwrite the frozen window
    row = _event_row(event_id)
    assert json.loads(row["evidence_snapshot_json"]) == synthetic


def test_restated_payload_drops_analysis_and_snapshot():
    """A changed payload supersedes the analysis AND its frozen window."""
    event_id, payload = _seed_cost_anomaly(occurred_on="2026-07-02")
    conn = db.connect_ready()
    try:
        with db.writing(conn):
            conn.execute(
                "UPDATE events SET analysis_json = '{}', "
                "evidence_snapshot_json = '[]' WHERE id = ?",
                (event_id,),
            )
        # identical payload: both survive (idempotent re-poll, cache-cheap)
        with db.writing(conn):
            same_id = db.upsert_event(
                conn,
                kind="cost_anomaly",
                service=payload["service"],
                occurred_on="2026-07-02",
                payload_json=json.dumps(payload),
                refresh_analysis_on_change=True,
            )
        assert same_id == event_id
        row = conn.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        assert row["analysis_json"] == "{}"
        assert row["evidence_snapshot_json"] == "[]"

        # re-stated payload: both fall as one audit unit
        restated = dict(payload, cost=999.0)
        with db.writing(conn):
            db.upsert_event(
                conn,
                kind="cost_anomaly",
                service=payload["service"],
                occurred_on="2026-07-02",
                payload_json=json.dumps(restated),
                refresh_analysis_on_change=True,
            )
        row = conn.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        assert row["analysis_json"] is None
        assert row["evidence_snapshot_json"] is None
    finally:
        conn.close()
