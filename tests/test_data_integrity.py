"""Storage-layer data-integrity contracts (dbt-style invariants).

app/db.py claims referential integrity, illegal states unrepresentable
and a race-safe natural key. test_db.py proves the pragmas are SET; this
suite proves they are ENFORCED — a dangling reference, an illegal verdict
or a duplicate signal must all be rejected at the storage layer, not left
to caller discipline. These are the "not_null / accepted_values /
referential" checks a warehouse would run, expressed against SQLite.
"""

import sqlite3

import pytest

from app import db

# The full committed schema (test_db.py's EXPECTED_TABLES covers the six
# core tables; the demo-ops and agent-feed tables must exist too).
ALL_TABLES = {
    "events",
    "actions",
    "decisions",
    "ai_usage",
    "llm_cache",
    "idempotency",
    "pulse_log",
    "agent_feed",
}


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "integrity.db"
    db.init_db(path)
    connection = db.connect(path)
    yield connection
    connection.close()


# --- schema completeness ----------------------------------------------------


def test_full_schema_is_present(conn):
    names = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert ALL_TABLES <= names


def test_integrity_check_passes_after_init(conn):
    """A freshly built database is internally consistent and free of
    dangling foreign keys."""
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


# --- accepted values (CHECK constraints) ------------------------------------


def test_decision_verdict_check_rejects_an_illegal_verdict(conn):
    """Symmetric to the tested actions.state CHECK: only 'approved' and
    'rejected' are representable — a stray verdict must be impossible."""
    with pytest.raises(sqlite3.IntegrityError):
        with db.writing(conn):
            conn.execute(
                "INSERT INTO decisions (service, verdict, input_context_json) "
                "VALUES (?, ?, ?)",
                ("ec2", "maybe", "{}"),
            )


# --- referential integrity (foreign_keys=ON is enforced, not just set) ------


def test_foreign_key_rejects_a_dangling_action_event(conn):
    with pytest.raises(sqlite3.IntegrityError):
        with db.writing(conn):
            conn.execute(
                "INSERT INTO actions (event_id, title, detail_json) "
                "VALUES (?, ?, ?)",
                (9999, "orphaned action", "{}"),
            )


def test_foreign_key_rejects_a_dangling_decision_action(conn):
    with pytest.raises(sqlite3.IntegrityError):
        with db.writing(conn):
            conn.execute(
                "INSERT INTO decisions "
                "(action_id, service, verdict, input_context_json) "
                "VALUES (?, ?, ?, ?)",
                (9999, "ec2", "approved", "{}"),
            )


def test_nullable_foreign_keys_are_allowed(conn):
    """The FK columns are nullable by design (seed/demo paths file actions
    without a persisted event); a NULL reference must NOT trip enforcement."""
    with db.writing(conn):
        conn.execute(
            "INSERT INTO actions (event_id, title, detail_json) "
            "VALUES (NULL, 'freestanding', '{}')"
        )
    assert conn.execute("SELECT count(*) FROM actions").fetchone()[0] == 1


# --- unique natural key (dedupe below the upsert helper) --------------------


def test_event_natural_key_rejects_a_raw_duplicate(conn):
    """upsert_event dedupes by design; the UNIQUE index is what makes a
    second RAW insert impossible, guarding a future writer that bypasses
    the helper."""
    with db.writing(conn):
        conn.execute(
            "INSERT INTO events (kind, service, occurred_on, payload_json) "
            "VALUES ('cost_anomaly', 'ec2', '2026-07-12', '{}')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        with db.writing(conn):
            conn.execute(
                "INSERT INTO events (kind, service, occurred_on, payload_json) "
                "VALUES ('cost_anomaly', 'ec2', '2026-07-12', '{}')"
            )
