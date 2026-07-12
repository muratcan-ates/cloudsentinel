"""Tests for the SQLite persistence core (app/db.py).

Acceptance criteria from the sprint plan: schema builds idempotently on
startup, data survives a restart (close + reopen), and concurrent
writers lose nothing and never see "database is locked".
"""

import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import db

EXPECTED_TABLES = {
    "events",
    "actions",
    "decisions",
    "ai_usage",
    "llm_cache",
    "idempotency",
}


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in rows}


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "core.db"
    db.init_db(path)
    connection = db.connect(path)
    yield connection
    connection.close()


# --- schema and connection posture ------------------------------------------


def test_init_db_creates_all_tables(conn):
    assert EXPECTED_TABLES <= table_names(conn)


def test_init_db_is_idempotent(tmp_path):
    path = tmp_path / "twice.db"
    db.init_db(path)
    db.init_db(path)  # second run must not raise or duplicate anything
    conn = db.connect(path)
    try:
        assert EXPECTED_TABLES <= table_names(conn)
    finally:
        conn.close()


def test_connection_pragmas(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    # synchronous: 1 == NORMAL
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == db.BUSY_TIMEOUT_MS
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_db_path_honors_env_override(monkeypatch):
    monkeypatch.setenv(db.DB_PATH_ENV, "/somewhere/else.db")
    assert str(db.db_path()) == "/somewhere/else.db"
    monkeypatch.delenv(db.DB_PATH_ENV)
    assert str(db.db_path()) == db.DEFAULT_DB_PATH


def test_action_state_check_constraint(conn):
    with pytest.raises(sqlite3.IntegrityError):
        with db.writing(conn):
            conn.execute(
                "INSERT INTO actions (title, detail_json, state) VALUES (?, ?, ?)",
                ("bad", "{}", "sideways"),
            )


# --- writing() transaction helper -------------------------------------------


def test_writing_commits_on_success(tmp_path):
    path = tmp_path / "commit.db"
    db.init_db(path)
    writer = db.connect(path)
    with db.writing(writer):
        writer.execute(
            "INSERT INTO events (kind, service, occurred_on, payload_json) "
            "VALUES ('cost_anomaly', 'ec2', '2026-07-12', '{}')"
        )
    writer.close()

    reader = db.connect(path)
    try:
        assert reader.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    finally:
        reader.close()


def test_writing_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError):
        with db.writing(conn):
            conn.execute(
                "INSERT INTO events (kind, service, occurred_on, payload_json) "
                "VALUES ('cost_anomaly', 'ec2', '2026-07-12', '{}')"
            )
            raise RuntimeError("boom")
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    # the transaction is fully closed: a fresh write must work
    with db.writing(conn):
        conn.execute(
            "INSERT INTO events (kind, service, occurred_on, payload_json) "
            "VALUES ('cost_anomaly', 's3', '2026-07-12', '{}')"
        )
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_writing_is_not_reentrant(conn):
    with pytest.raises(sqlite3.OperationalError):
        with db.writing(conn):
            with db.writing(conn):
                pass  # pragma: no cover


def test_writing_takes_write_lock_up_front(tmp_path):
    """BEGIN IMMEDIATE must reserve the write lock at entry — a deferred
    BEGIN would only take it at the first write statement, reintroducing
    mid-transaction 'database is locked' upgrades."""
    path = tmp_path / "lock.db"
    db.init_db(path)
    holder = db.connect(path)
    contender = db.connect(path)
    contender.execute("PRAGMA busy_timeout=100")
    try:
        with db.writing(holder):
            # no write statement has run yet, but the lock must be held
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                contender.execute("BEGIN IMMEDIATE")
        contender.execute("BEGIN IMMEDIATE")  # released after commit
        contender.execute("COMMIT")
    finally:
        holder.close()
        contender.close()


def test_connection_is_usable_across_threads(conn):
    """check_same_thread=False is load-bearing: FastAPI's threadpool may
    touch a connection on a different thread than the one that opened it."""
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(lambda: conn.execute("SELECT 1").fetchone()[0]).result()
    assert result == 1


# --- idempotency ------------------------------------------------------------


def test_idempotency_first_claim_then_replay(conn):
    with db.writing(conn):
        claimed, stored = db.claim_idempotency(conn, "approve-42")
        assert claimed is True
        assert stored is None
        db.store_idempotency_response(conn, "approve-42", '{"state": "approved"}')

    with db.writing(conn):
        claimed, stored = db.claim_idempotency(conn, "approve-42")
    assert claimed is False
    assert json.loads(stored) == {"state": "approved"}


def test_idempotency_claim_rolls_back_with_failed_work(conn):
    """A failed transaction must release the key for a clean retry."""
    with pytest.raises(RuntimeError):
        with db.writing(conn):
            claimed, _ = db.claim_idempotency(conn, "retry-me")
            assert claimed is True
            raise RuntimeError("work failed")

    with db.writing(conn):
        claimed, stored = db.claim_idempotency(conn, "retry-me")
    assert claimed is True
    assert stored is None


def test_idempotency_null_key_is_rejected(conn):
    """A NULL key must never 'claim': NULLs are pairwise distinct in SQLite,
    so without NOT NULL every NULL insert would silently succeed and defeat
    deduplication entirely."""
    with pytest.raises(sqlite3.IntegrityError):
        with db.writing(conn):
            db.claim_idempotency(conn, None)


def test_idempotency_concurrent_double_post(tmp_path):
    """Two racing claims on one key: exactly one wins (WP-5a precursor)."""
    path = tmp_path / "race.db"
    db.init_db(path)
    barrier = threading.Barrier(2)

    def contend(worker: int):
        conn = db.connect(path)
        try:
            # timeout: a pre-barrier failure must break the barrier and fail
            # the test instead of parking the other worker forever
            barrier.wait(timeout=30)
            with db.writing(conn):
                claimed, stored = db.claim_idempotency(conn, "double-post")
                if claimed:
                    db.store_idempotency_response(
                        conn, "double-post", json.dumps({"winner": worker})
                    )
            return claimed, stored
        finally:
            conn.close()

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(contend, range(2)))

    claims = sorted(claimed for claimed, _ in results)
    assert claims == [False, True]
    loser_stored = next(stored for claimed, stored in results if not claimed)
    assert json.loads(loser_stored)["winner"] in (0, 1)


# --- llm_cache --------------------------------------------------------------


def test_cache_roundtrip(conn):
    assert db.cache_get(conn, "gemini-2.5-flash", "explain this") is None
    db.cache_put(
        conn,
        "gemini-2.5-flash",
        "explain this",
        "an explanation",
        response_json='{"confidence": 0.9}',
    )
    row = db.cache_get(conn, "gemini-2.5-flash", "explain this")
    assert row["response_text"] == "an explanation"
    assert json.loads(row["response_json"]) == {"confidence": 0.9}


def test_cache_key_distinguishes_model_prompt_and_system(conn):
    db.cache_put(conn, "model-a", "prompt", "answer-a")
    assert db.cache_get(conn, "model-b", "prompt") is None
    assert db.cache_get(conn, "model-a", "other prompt") is None
    assert db.cache_get(conn, "model-a", "prompt", system_instruction="s") is None
    # concatenation ambiguity must not collide
    assert db.cache_key("ab", "c") != db.cache_key("a", "bc")


def test_cache_put_refreshes_existing_entry(conn):
    db.cache_put(conn, "m", "p", "first")
    db.cache_put(conn, "m", "p", "second")
    assert db.cache_get(conn, "m", "p")["response_text"] == "second"


def test_cache_roundtrip_with_system_instruction(conn):
    """put and get must derive the key identically when a system
    instruction is present (guards the put-side key computation)."""
    db.cache_put(conn, "m", "p", "sys-answer", system_instruction="be terse")
    row = db.cache_get(conn, "m", "p", system_instruction="be terse")
    assert row is not None
    assert row["response_text"] == "sys-answer"


# --- ai_usage ---------------------------------------------------------------


def test_record_ai_usage(conn):
    db.record_ai_usage(
        conn,
        agent="analyst",
        model="gemini-2.5-flash",
        source="fake",
        prompt="triage this anomaly",
        from_cache=True,
    )
    row = conn.execute("SELECT * FROM ai_usage").fetchone()
    assert row["agent"] == "analyst"
    assert row["source"] == "fake"
    assert row["from_cache"] == 1
    assert len(row["prompt_sha256"]) == 64
    assert "triage" not in row["prompt_sha256"]  # only the hash is stored


# --- acceptance: restart persistence ----------------------------------------


def test_restart_persistence(tmp_path):
    """Rows written before a 'restart' are all there after reopen + init."""
    path = tmp_path / "restart.db"
    db.init_db(path)
    conn = db.connect(path)
    with db.writing(conn):
        conn.execute(
            "INSERT INTO events (kind, service, occurred_on, payload_json) "
            "VALUES ('cost_anomaly', 'ec2', '2026-07-12', '{\"z\": 4.2}')"
        )
        conn.execute(
            "INSERT INTO actions (event_id, title, detail_json) VALUES (1, 'scale down', '{}')"
        )
        db.claim_idempotency(conn, "seen-before")
        db.store_idempotency_response(conn, "seen-before", "{}")
    db.cache_put(conn, "m", "p", "cached answer")
    conn.close()

    db.init_db(path)  # startup must not clobber existing data
    conn = db.connect(path)
    try:
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert conn.execute("SELECT state FROM actions").fetchone()[0] == "proposed"
        with db.writing(conn):
            claimed, _ = db.claim_idempotency(conn, "seen-before")
        assert claimed is False
        assert db.cache_get(conn, "m", "p")["response_text"] == "cached answer"
    finally:
        conn.close()


def test_init_db_creates_missing_directories(tmp_path):
    """A DB path inside a not-yet-existing tree must work on first boot."""
    path = tmp_path / "data" / "nested" / "fresh.db"
    db.init_db(path)
    conn = db.connect(path)
    try:
        assert EXPECTED_TABLES <= table_names(conn)
    finally:
        conn.close()


def test_seed_on_startup_rebuilds_schema_after_disk_loss(tmp_path):
    """Ephemeral deploy disk: a vanished file must come back with full schema."""
    path = tmp_path / "ephemeral.db"
    db.init_db(path)
    for suffix in ("", "-wal", "-shm"):
        sidecar = tmp_path / f"ephemeral.db{suffix}"
        if sidecar.exists():
            sidecar.unlink()

    db.init_db(path)
    conn = db.connect(path)
    try:
        assert EXPECTED_TABLES <= table_names(conn)
    finally:
        conn.close()


# --- acceptance: concurrent writers -----------------------------------------


def test_concurrent_writers_lose_nothing(tmp_path):
    """N threads × M writes: every row lands, nobody sees 'database is locked'."""
    path = tmp_path / "concurrent.db"
    db.init_db(path)
    threads, per_thread = 8, 25
    barrier = threading.Barrier(threads)

    def hammer(worker: int):
        conn = db.connect(path)
        try:
            barrier.wait(timeout=30)
            for i in range(per_thread):
                with db.writing(conn):
                    conn.execute(
                        "INSERT INTO events (kind, service, occurred_on, payload_json) "
                        "VALUES ('cost_anomaly', ?, '2026-07-12', '{}')",
                        (f"svc-{worker}-{i}",),
                    )
        finally:
            conn.close()

    with ThreadPoolExecutor(threads) as pool:
        # materialize results so any exception (e.g. OperationalError) surfaces
        list(pool.map(hammer, range(threads)))

    conn = db.connect(path)
    try:
        count = conn.execute("SELECT count(*) FROM events").fetchone()[0]
        distinct = conn.execute("SELECT count(DISTINCT service) FROM events").fetchone()[0]
    finally:
        conn.close()
    assert count == threads * per_thread
    assert distinct == threads * per_thread


# --- startup wiring ----------------------------------------------------------


def test_lifespan_initializes_schema(tmp_path, monkeypatch):
    """Running the app through its lifespan builds the schema at the env path."""
    path = tmp_path / "lifespan.db"
    monkeypatch.setenv(db.DB_PATH_ENV, str(path))
    from main import app

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    conn = db.connect(path)
    try:
        assert EXPECTED_TABLES <= table_names(conn)
    finally:
        conn.close()


def test_get_db_dependency_yields_and_closes():
    gen = db.get_db()
    conn = next(gen)
    assert conn.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(StopIteration):
        next(gen)
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")  # closed


def test_db_isolation_fixture_routes_into_pytest_tmp():
    """The autouse fixture must route the resolved path into pytest's tmp
    tree so no test can ever touch a developer's real cloudsentinel.db."""
    resolved = db.db_path()
    assert resolved.name == "test.db"
    assert str(resolved) != db.DEFAULT_DB_PATH
    assert "pytest" in str(resolved)
    assert os.environ["SENTINEL_DB_PATH"] == str(resolved)
