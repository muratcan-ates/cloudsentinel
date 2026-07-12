"""SQLite persistence core for CloudSentinel.

Design constraints (locked in docs/architecture.md and the sprint plan):
- stdlib ``sqlite3`` only; WAL journal with ``synchronous=NORMAL`` and a
  5s busy timeout so FastAPI's worker threads never hit
  "database is locked" under normal contention.
- Every writing transaction opens with ``BEGIN IMMEDIATE`` so the write
  lock is taken up front instead of failing mid-transaction on upgrade.
- Connections are opened per use (cheap under WAL) instead of shared
  across threads, so transactions can never interleave.
- Idempotency uses ``INSERT ... ON CONFLICT DO NOTHING RETURNING``
  (race-safe; requires sqlite >= 3.35). Side-effectful work must run
  only after the claim succeeds, inside the same transaction.
- Never call the LLM or the network inside an open transaction — it
  holds the write lock for the whole call.
- ``init_db()`` is idempotent and runs at startup: the deployment
  target's filesystem is ephemeral, so the schema must rebuild itself
  from nothing on every boot (seed-on-startup).
- ``PRAGMA foreign_keys=ON`` is a deliberate addition beyond the locked
  pragma set: referential integrity between actions/decisions/events is
  enforced at the storage layer instead of by caller discipline.
- Recorded carve-out from the BEGIN IMMEDIATE rule: single-statement
  helper writes (``cache_put``, ``record_ai_usage``) may run in
  autocommit — SQLite serializes a lone statement through the busy
  handler just fine. Any multi-statement unit of work MUST go through
  ``writing()``; composing these helpers into one logical unit without
  it forfeits atomicity.
"""

import hashlib
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DB_PATH_ENV = "SENTINEL_DB_PATH"
DEFAULT_DB_PATH = "cloudsentinel.db"

BUSY_TIMEOUT_MS = 5000

# Action lifecycle (docs/architecture.md): every transition is persisted
# with timestamp and actor; the CHECK constraint makes illegal states
# unrepresentable at the storage layer.
ACTION_STATES = ("proposed", "approved", "rejected", "executed")

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        service TEXT NOT NULL,
        occurred_on TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        analysis_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY,
        event_id INTEGER REFERENCES events(id),
        title TEXT NOT NULL,
        detail_json TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'proposed'
            CHECK (state IN ('proposed', 'approved', 'rejected', 'executed')),
        proposed_at TEXT NOT NULL DEFAULT (datetime('now')),
        decided_at TEXT,
        decided_by TEXT,
        executed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id INTEGER PRIMARY KEY,
        action_id INTEGER REFERENCES actions(id),
        service TEXT NOT NULL,
        verdict TEXT NOT NULL CHECK (verdict IN ('approved', 'rejected')),
        rationale TEXT,
        input_context_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_usage (
        id INTEGER PRIMARY KEY,
        agent TEXT NOT NULL,
        model TEXT NOT NULL,
        source TEXT NOT NULL,
        prompt_sha256 TEXT NOT NULL,
        from_cache INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_cache (
        key TEXT PRIMARY KEY NOT NULL,
        model TEXT NOT NULL,
        response_text TEXT NOT NULL,
        response_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS idempotency (
        key TEXT PRIMARY KEY NOT NULL,
        response_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_actions_state ON actions(state)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_service ON decisions(service)",
    # Natural key: rescans must yield the same event id for the same signal,
    # so POST /anomalies/{id}/analyze has stable, bookmarkable targets.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_natural_key "
    "ON events(kind, service, occurred_on)",
)


def db_path() -> Path:
    """Resolve the database file path, honoring the env override."""
    return Path(os.environ.get(DB_PATH_ENV, "").strip() or DEFAULT_DB_PATH)


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open a new connection with the locked pragma set applied.

    ``isolation_level=None`` puts the connection in autocommit mode so
    transactions are controlled explicitly via ``writing()`` — the
    sqlite3 module's implicit transaction management would otherwise
    issue plain ``BEGIN`` (deferred) behind our back.
    """
    target = Path(path or db_path())
    # Ephemeral deploy disks can hand us a fresh tree: sqlite's bare
    # "unable to open database file" names no path, so create the parent
    # directory up front instead of failing cryptically at startup.
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(target),
        timeout=BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def writing(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a write transaction: BEGIN IMMEDIATE, commit on success.

    Rolls back and re-raises on any exception. Not reentrant — nesting
    ``writing()`` on the same connection raises ``sqlite3.OperationalError``
    ("cannot start a transaction within a transaction"), which is the
    behavior we want: nested write scopes are a design error.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def init_db(path: Path | str | None = None) -> None:
    """Create the schema if missing (idempotent, safe on every startup)."""
    conn = connect(path)
    try:
        with writing(conn):
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
            # CREATE TABLE IF NOT EXISTS never alters an existing table, so
            # top up columns added after a table first shipped (dev DBs
            # predate the ephemeral-disk reset cycle).
            events_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(events)")
            }
            if "analysis_json" not in events_columns:
                conn.execute("ALTER TABLE events ADD COLUMN analysis_json TEXT")
    finally:
        conn.close()


_INITIALIZED_PATHS: set[str] = set()


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request, always closed.

    Initializes the schema once per resolved path, so endpoints stay
    correct even when the app runs without its lifespan (module-level
    test clients); redundant with the startup hook, and idempotent.
    """
    target = str(db_path())
    if target not in _INITIALIZED_PATHS:
        init_db(target)
        _INITIALIZED_PATHS.add(target)
    conn = connect(target)
    try:
        yield conn
    finally:
        conn.close()


def upsert_event(
    conn: sqlite3.Connection,
    *,
    kind: str,
    service: str,
    occurred_on: str,
    payload_json: str,
) -> int:
    """Insert or refresh an event by natural key; returns its stable id.

    Re-detections update the payload (costs may be re-stated) but keep
    the id, so analyses and actions stay attached across rescans. Call
    inside an open ``writing()`` transaction.
    """
    row = conn.execute(
        "INSERT INTO events (kind, service, occurred_on, payload_json) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(kind, service, occurred_on) DO UPDATE SET "
        "payload_json = excluded.payload_json "
        "RETURNING id",
        (kind, service, occurred_on, payload_json),
    ).fetchone()
    return row["id"]


# --- Idempotency ------------------------------------------------------------
#
# Usage pattern (single writing() transaction, race-safe):
#
#     with writing(conn):
#         claimed, stored = claim_idempotency(conn, key)
#         if not claimed:
#             return stored            # replay: first response, verbatim
#         response = do_the_work(conn) # DB-only side effects, same txn
#         store_idempotency_response(conn, key, response)
#     return response
#
# BEGIN IMMEDIATE serializes writers, so a concurrent duplicate blocks
# until the first transaction commits and then sees the stored response.


def claim_idempotency(conn: sqlite3.Connection, key: str) -> tuple[bool, str | None]:
    """Atomically claim ``key``.

    Returns ``(True, None)`` when this caller owns the claim and must do
    the work, or ``(False, stored_response_json)`` when the key was
    already claimed. Call inside an open ``writing()`` transaction.
    """
    row = conn.execute(
        "INSERT INTO idempotency (key) VALUES (?) "
        "ON CONFLICT(key) DO NOTHING RETURNING key",
        (key,),
    ).fetchone()
    if row is not None:
        return True, None
    stored = conn.execute(
        "SELECT response_json FROM idempotency WHERE key = ?", (key,)
    ).fetchone()
    return False, stored["response_json"] if stored else None


def store_idempotency_response(
    conn: sqlite3.Connection, key: str, response_json: str
) -> None:
    """Record the canonical response for a claimed key (same transaction)."""
    conn.execute(
        "UPDATE idempotency SET response_json = ? WHERE key = ?",
        (response_json, key),
    )


# --- LLM response cache -----------------------------------------------------


def cache_key(model: str, prompt: str, system_instruction: str = "") -> str:
    """SHA-256 over model + system instruction + prompt.

    NUL separators prevent ambiguous concatenations (e.g. model "a"
    with prompt "bc" colliding with model "ab" and prompt "c").
    """
    material = "\x00".join((model, system_instruction, prompt))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def cache_get(
    conn: sqlite3.Connection,
    model: str,
    prompt: str,
    system_instruction: str = "",
) -> sqlite3.Row | None:
    """Return the cached row for this exact request, or None on miss."""
    return conn.execute(
        "SELECT key, model, response_text, response_json, created_at "
        "FROM llm_cache WHERE key = ?",
        (cache_key(model, prompt, system_instruction),),
    ).fetchone()


def cache_put(
    conn: sqlite3.Connection,
    model: str,
    prompt: str,
    response_text: str,
    response_json: str | None = None,
    system_instruction: str = "",
) -> None:
    """Store (or refresh) the cached response for this exact request."""
    conn.execute(
        "INSERT INTO llm_cache (key, model, response_text, response_json) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET "
        "response_text = excluded.response_text, "
        "response_json = excluded.response_json, "
        "created_at = datetime('now')",
        (cache_key(model, prompt, system_instruction), model, response_text, response_json),
    )


# --- AI usage ledger --------------------------------------------------------


def record_ai_usage(
    conn: sqlite3.Connection,
    *,
    agent: str,
    model: str,
    source: str,
    prompt: str,
    from_cache: bool = False,
) -> None:
    """Append one row to the AI usage ledger.

    Only a hash of the prompt is stored — the ledger tracks quota and
    provenance, not content. Joins the caller's open transaction when
    there is one, otherwise autocommits.
    """
    conn.execute(
        "INSERT INTO ai_usage (agent, model, source, prompt_sha256, from_cache) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            agent,
            model,
            source,
            hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            int(from_cache),
        ),
    )
