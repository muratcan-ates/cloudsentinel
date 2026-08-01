"""The audit chain: sealing at write time, and catching every way it breaks.

Each tampering test edits the SQLite file the way an attacker with disk
access would — behind the application, never through an endpoint — which
is exactly the threat the hash chain exists to make visible.
"""

import json

from fastapi.testclient import TestClient

from app import db, history, ledger
from main import app

client = TestClient(app)


def _fresh_conn():
    conn = db.connect_ready()
    ledger.ensure_schema(conn)
    return conn


def _file_an_action(conn, title="right-size the compute tier"):
    with db.writing(conn):
        cursor = conn.execute(
            "INSERT INTO actions (event_id, title, detail_json) VALUES (NULL, ?, ?)",
            (title, json.dumps({"anomaly": {"service": "compute"}})),
        )
        action_id = cursor.lastrowid
        history.record(conn, action_id, "filed", "agent:recommender")
    return action_id


def _decide(conn, action_id, verdict="approved", rationale="owner confirmed"):
    with db.writing(conn):
        conn.execute(
            "INSERT INTO decisions "
            "(action_id, service, verdict, rationale, input_context_json) "
            "VALUES (?, 'compute', ?, ?, '{}')",
            (action_id, verdict, rationale),
        )
        history.record(conn, action_id, verdict, "operator:murat", rationale)


def test_a_transition_seals_itself_into_the_chain():
    conn = _fresh_conn()
    try:
        _file_an_action(conn)
        entries = conn.execute("SELECT * FROM audit_ledger ORDER BY id").fetchall()
        assert len(entries) == 1
        assert entries[0]["stream"] == "action_event"
        assert entries[0]["prev_hash"] == ledger.GENESIS_HASH
        assert len(entries[0]["entry_hash"]) == 64
    finally:
        conn.close()


def test_a_verdict_is_sealed_by_the_transition_that_records_it():
    """The decide path writes the decision first, then the transition —
    one stamp call seals both, so no caller has to remember the ledger."""
    conn = _fresh_conn()
    try:
        action_id = _file_an_action(conn)
        _decide(conn, action_id)
        streams = [
            row["stream"]
            for row in conn.execute("SELECT stream FROM audit_ledger ORDER BY id")
        ]
        assert streams == ["action_event", "decision", "action_event"]
    finally:
        conn.close()


def test_each_link_carries_the_previous_hash():
    conn = _fresh_conn()
    try:
        action_id = _file_an_action(conn)
        _decide(conn, action_id)
        entries = conn.execute("SELECT * FROM audit_ledger ORDER BY id").fetchall()
        for earlier, later in zip(entries, entries[1:]):
            assert later["prev_hash"] == earlier["entry_hash"]
        assert ledger.head(conn) == entries[-1]["entry_hash"]
    finally:
        conn.close()


def test_sealing_is_idempotent():
    """A second stamp adds nothing: rows already in the chain stay put."""
    conn = _fresh_conn()
    try:
        _file_an_action(conn)
        with db.writing(conn):
            assert ledger.stamp(conn) == 0
    finally:
        conn.close()


def test_an_untouched_chain_verifies():
    conn = _fresh_conn()
    try:
        action_id = _file_an_action(conn)
        _decide(conn, action_id)
        report = ledger.verify(conn)
        assert report.ok is True
        assert report.first_break is None
        assert report.entries == 3
        assert report.verified == 3
        assert report.unsealed_total == 0
    finally:
        conn.close()


def test_an_empty_chain_verifies_at_genesis():
    conn = _fresh_conn()
    try:
        report = ledger.verify(conn)
        assert report.ok is True
        assert report.entries == 0
        assert report.head == ledger.GENESIS_HASH
    finally:
        conn.close()


def test_a_rewritten_verdict_is_caught():
    conn = _fresh_conn()
    try:
        action_id = _file_an_action(conn)
        _decide(conn, action_id, "rejected", "no owner sign-off")
        # Straight at the file, behind the app: flip the recorded verdict.
        with db.writing(conn):
            conn.execute("UPDATE decisions SET verdict = 'approved'")
        report = ledger.verify(conn)
        assert report.ok is False
        assert report.first_break.reason == "source_modified"
        assert report.first_break.stream == "decision"
        assert "decisions row" in report.first_break.detail
    finally:
        conn.close()


def test_a_rewritten_rationale_is_caught_too():
    """The chain seals the whole row, not just the verdict column."""
    conn = _fresh_conn()
    try:
        action_id = _file_an_action(conn)
        _decide(conn, action_id, "approved", "owner confirmed idle capacity")
        with db.writing(conn):
            conn.execute("UPDATE decisions SET rationale = 'routine cleanup'")
        report = ledger.verify(conn)
        assert report.ok is False
        assert report.first_break.reason == "source_modified"
    finally:
        conn.close()


def test_a_deleted_transition_is_caught():
    conn = _fresh_conn()
    try:
        action_id = _file_an_action(conn)
        _decide(conn, action_id)
        with db.writing(conn):
            conn.execute("DELETE FROM action_events WHERE transition = 'approved'")
        report = ledger.verify(conn)
        assert report.ok is False
        assert report.first_break.reason == "source_deleted"
        assert report.first_break.stream == "action_event"
    finally:
        conn.close()


def test_a_spliced_out_ledger_entry_is_caught():
    conn = _fresh_conn()
    try:
        action_id = _file_an_action(conn)
        _decide(conn, action_id)
        # Drop the middle link and leave the sources intact: only the
        # prev_hash arithmetic can see this.
        with db.writing(conn):
            conn.execute("DELETE FROM audit_ledger WHERE id = 2")
        report = ledger.verify(conn)
        assert report.ok is False
        assert report.first_break.reason == "chain_break"
        assert report.verified == 1
    finally:
        conn.close()


def test_a_forged_ledger_body_is_caught():
    """Rewriting the source AND its sealed body still fails: the entry
    hash was computed over the original bytes."""
    conn = _fresh_conn()
    try:
        action_id = _file_an_action(conn)
        _decide(conn, action_id, "rejected", "no owner sign-off")
        with db.writing(conn):
            conn.execute("UPDATE decisions SET verdict = 'approved'")
            row = conn.execute(
                "SELECT id, action_id, service, verdict, rationale, "
                "input_context_json, created_at FROM decisions"
            ).fetchone()
            forged = ledger._canonical_body(row, ledger.SEALED_STREAMS["decision"][1])
            conn.execute(
                "UPDATE audit_ledger SET body = ? WHERE stream = 'decision'", (forged,)
            )
        report = ledger.verify(conn)
        assert report.ok is False
        assert report.first_break.reason == "entry_rewritten"
    finally:
        conn.close()


def test_rows_that_never_reached_the_desk_are_reported_unsealed():
    """Seeded demo verdicts are injected, not decided — the chain says so
    instead of quietly absorbing them."""
    conn = _fresh_conn()
    try:
        with db.writing(conn):
            conn.execute(
                "INSERT INTO decisions "
                "(action_id, service, verdict, rationale, input_context_json) "
                "VALUES (NULL, 'storage', 'approved', 'seeded demo verdict', '{}')"
            )
        report = ledger.verify(conn)
        assert report.ok is True
        assert report.unsealed_total == 1
        decisions = next(s for s in report.streams if s.stream == "decision")
        assert decisions.sealed == 0
        assert decisions.unsealed == 1
    finally:
        conn.close()


def test_verify_endpoint_publishes_the_computation():
    response = client.get("/audit/verify")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["head"] == ledger.GENESIS_HASH
    assert "sha256" in body["method"]
    assert {s["stream"] for s in body["streams"]} == {"decision", "action_event"}


def test_verify_endpoint_reports_a_break_over_http():
    conn = _fresh_conn()
    try:
        action_id = _file_an_action(conn)
        _decide(conn, action_id, "rejected", "no owner sign-off")
        with db.writing(conn):
            conn.execute("UPDATE decisions SET verdict = 'approved'")
    finally:
        conn.close()
    body = client.get("/audit/verify").json()
    assert body["ok"] is False
    assert body["first_break"]["reason"] == "source_modified"


def test_demo_reset_clears_the_chain_with_the_rows_it_sealed(monkeypatch):
    """A stage reset must not read as tampering."""
    monkeypatch.setenv("SENTINEL_DEMO_RESET", "1")
    conn = _fresh_conn()
    try:
        action_id = _file_an_action(conn)
        _decide(conn, action_id)
    finally:
        conn.close()
    assert client.post("/ops/demo-reset").status_code == 200
    body = client.get("/audit/verify").json()
    assert body["ok"] is True
    assert body["entries"] == 0
    assert body["head"] == ledger.GENESIS_HASH


def test_the_chain_restarts_cleanly_after_a_reset(monkeypatch):
    """SQLite restarts rowids at 1 after the wipe; the anti-join sealer
    must still pick the new rows up (a high-water mark would not)."""
    monkeypatch.setenv("SENTINEL_DEMO_RESET", "1")
    conn = _fresh_conn()
    try:
        _decide(conn, _file_an_action(conn))
    finally:
        conn.close()
    client.post("/ops/demo-reset")
    conn = _fresh_conn()
    try:
        _decide(conn, _file_an_action(conn))
    finally:
        conn.close()
    body = client.get("/audit/verify").json()
    assert body["ok"] is True
    assert body["entries"] == 3
    assert body["unsealed_total"] == 0
