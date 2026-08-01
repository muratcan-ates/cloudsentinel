"""Tamper-evident audit chain over the decision record (Sprint 3, D1).

Everywhere else in this project the audit trail is described as
"append-only". That is an *architectural* claim: no code path updates or
deletes a row in ``decisions`` or ``action_events``. It is also
unfalsifiable from the outside — anyone with the database file can open
it in ``sqlite3`` and rewrite a verdict, and nothing in the product would
notice.

This module replaces the claim with an arithmetic one. Every sealed row
is hashed together with the hash of the entry before it, so the ledger is
a chain: change one byte of a sealed row, or splice or drop an entry, and
every hash from that point on stops matching. ``GET /audit/verify`` walks
the chain from genesis and reports the FIRST broken link — which entry,
which source row, and which of the four ways it broke.

Four distinct failures are detectable, and the endpoint names them apart:

- ``chain_break``     — an entry's ``prev_hash`` is not its predecessor's
                        ``entry_hash``: an entry was spliced in or removed.
- ``entry_rewritten`` — the entry's own ``entry_hash`` does not match its
                        recorded contents: the ledger row itself was edited.
- ``source_modified`` — the ledger is internally consistent but the live
                        row in ``decisions`` / ``action_events`` no longer
                        matches what was sealed: the source was rewritten.
- ``source_deleted``  — the sealed source row is gone entirely.

Sealing happens at WRITE time, inside the caller's transaction, driven by
``history.record`` — every lifecycle transition seals itself and any
decision row written alongside it in the same unit of work. Nothing seals
on read: a chain that sealed itself when you asked about it would only
ever prove the read was self-consistent.

Rows that never came through the decision desk therefore stay OUTSIDE the
chain and are reported as ``unsealed`` rather than silently absorbed —
the ``?seed=1`` demo verdicts of ``POST /ops/demo-reset`` are the honest
example: they were injected by the reset tool, not decided by a human, so
they are visibly not part of the chain.

``hashlib`` is stdlib; this costs no new dependency. The table is created
lazily here rather than in ``db._SCHEMA_STATEMENTS`` so the chain and its
storage stay one auditable unit.
"""

import hashlib
import json
import sqlite3

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import db

router = APIRouter(prefix="/audit", tags=["audit"])

# The prev_hash of the very first entry. A real digest is 64 hex chars, so
# an all-zero string can never be produced by SHA-256 — genesis is
# unforgeable as a position, not just as a value.
GENESIS_HASH = "0" * 64

# stream name -> (source table, the columns whose bytes are sealed).
# Sealing every column of the row is deliberate: a chain that covered only
# the verdict would let someone rewrite the rationale for free.
SEALED_STREAMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "decision": (
        "decisions",
        (
            "id",
            "action_id",
            "service",
            "verdict",
            "rationale",
            "input_context_json",
            "created_at",
        ),
    ),
    "action_event": (
        "action_events",
        ("id", "action_id", "transition", "actor", "note", "created_at"),
    ),
}

# Decisions before their own lifecycle events: within one transaction the
# verdict is the cause and the transition is the record of it.
STREAM_ORDER = ("decision", "action_event")

VERIFY_METHOD = (
    "SHA-256 chain: entry_hash = sha256(prev_hash | stream | ref_id | "
    "canonical row body), verified from genesis against the live source "
    "rows — the ledger is not asserted intact, it is recomputed"
)

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS audit_ledger (
        id INTEGER PRIMARY KEY,
        stream TEXT NOT NULL,
        ref_id INTEGER NOT NULL,
        body TEXT NOT NULL,
        prev_hash TEXT NOT NULL,
        entry_hash TEXT NOT NULL,
        sealed_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_ledger_ref "
    "ON audit_ledger(stream, ref_id)",
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the ledger table if missing (idempotent, joins any txn)."""
    for statement in _SCHEMA_STATEMENTS:
        conn.execute(statement)


def _canonical_body(row: sqlite3.Row, columns: tuple[str, ...]) -> str:
    """The exact bytes a row is sealed as.

    Sorted keys and tight separators so the same row always canonicalises
    identically — a body that depended on dict ordering would make the
    chain break at random and the whole guarantee worthless.
    """
    return json.dumps(
        {column: row[column] for column in columns},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _digest(prev_hash: str, stream: str, ref_id: int, body: str) -> str:
    """Hash one link. NUL separators keep the fields unambiguous."""
    material = "\x00".join((prev_hash, stream, str(ref_id), body))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def head(conn: sqlite3.Connection) -> str:
    """The current tip of the chain, or genesis when nothing is sealed."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT entry_hash FROM audit_ledger ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row["entry_hash"] if row else GENESIS_HASH


def _unsealed_rows(conn: sqlite3.Connection, stream: str) -> list[sqlite3.Row]:
    """Source rows of ``stream`` that are not in the chain yet, oldest first.

    Anti-joined on ``ref_id`` rather than compared against a high-water
    mark: ``POST /ops/demo-reset`` empties the source tables, so SQLite
    restarts rowids at 1 and a watermark would leave every row after a
    reset permanently unsealed.
    """
    table, columns = SEALED_STREAMS[stream]
    return conn.execute(
        f"SELECT {', '.join(columns)} FROM {table} "  # noqa: S608 — fixed column map
        "WHERE id NOT IN (SELECT ref_id FROM audit_ledger WHERE stream = ?) "
        "ORDER BY id",
        (stream,),
    ).fetchall()


def stamp(conn: sqlite3.Connection) -> int:
    """Seal every source row not yet in the chain; returns entries added.

    Call inside the writing transaction that produced the rows, so the
    row and its seal commit or roll back together — a decision that
    committed without its link would look like tampering forever after.
    """
    ensure_schema(conn)
    prev_hash = head(conn)
    added = 0
    for stream in STREAM_ORDER:
        _, columns = SEALED_STREAMS[stream]
        for row in _unsealed_rows(conn, stream):
            body = _canonical_body(row, columns)
            entry_hash = _digest(prev_hash, stream, row["id"], body)
            conn.execute(
                "INSERT INTO audit_ledger (stream, ref_id, body, prev_hash, entry_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (stream, row["id"], body, prev_hash, entry_hash),
            )
            prev_hash = entry_hash
            added += 1
    return added


class LedgerBreak(BaseModel):
    ledger_id: int
    stream: str
    ref_id: int
    reason: str  # chain_break | entry_rewritten | source_modified | source_deleted
    detail: str


class LedgerStreamStat(BaseModel):
    stream: str
    source_table: str
    sealed: int
    unsealed: int


class AuditVerifyReport(BaseModel):
    ok: bool
    method: str
    entries: int
    verified: int
    head: str
    streams: list[LedgerStreamStat]
    unsealed_total: int
    first_break: LedgerBreak | None
    note: str


def verify(conn: sqlite3.Connection) -> AuditVerifyReport:
    """Recompute the whole chain and report the first link that fails."""
    ensure_schema(conn)
    entries = conn.execute(
        "SELECT id, stream, ref_id, body, prev_hash, entry_hash "
        "FROM audit_ledger ORDER BY id"
    ).fetchall()

    # One read per stream instead of one per entry: the walk below is a
    # verification pass, not a query storm.
    live: dict[str, dict[int, str]] = {}
    for stream, (table, columns) in SEALED_STREAMS.items():
        live[stream] = {
            row["id"]: _canonical_body(row, columns)
            for row in conn.execute(
                f"SELECT {', '.join(columns)} FROM {table}"  # noqa: S608 — fixed map
            )
        }

    prev_hash = GENESIS_HASH
    verified = 0
    first_break: LedgerBreak | None = None
    for entry in entries:
        table = SEALED_STREAMS[entry["stream"]][0]
        current = live.get(entry["stream"], {}).get(entry["ref_id"])
        reason, detail = None, ""
        if entry["prev_hash"] != prev_hash:
            reason, detail = (
                "chain_break",
                "this entry does not follow the one before it — an entry "
                "was inserted or removed",
            )
        elif _digest(
            prev_hash, entry["stream"], entry["ref_id"], entry["body"]
        ) != entry["entry_hash"]:
            reason, detail = (
                "entry_rewritten",
                "the ledger row's own hash does not match its contents",
            )
        elif current is None:
            reason, detail = (
                "source_deleted",
                f"{table} row {entry['ref_id']} was sealed but no longer exists",
            )
        elif current != entry["body"]:
            reason, detail = (
                "source_modified",
                f"{table} row {entry['ref_id']} no longer matches the bytes "
                "that were sealed",
            )
        if reason is not None:
            first_break = LedgerBreak(
                ledger_id=entry["id"],
                stream=entry["stream"],
                ref_id=entry["ref_id"],
                reason=reason,
                detail=detail,
            )
            break
        prev_hash = entry["entry_hash"]
        verified += 1

    sealed_counts = {
        row["stream"]: row["n"]
        for row in conn.execute(
            "SELECT stream, count(*) AS n FROM audit_ledger GROUP BY stream"
        )
    }
    streams = [
        LedgerStreamStat(
            stream=stream,
            source_table=SEALED_STREAMS[stream][0],
            sealed=sealed_counts.get(stream, 0),
            unsealed=len(_unsealed_rows(conn, stream)),
        )
        for stream in STREAM_ORDER
    ]
    return AuditVerifyReport(
        ok=first_break is None,
        method=VERIFY_METHOD,
        entries=len(entries),
        verified=verified,
        head=head(conn),
        streams=streams,
        unsealed_total=sum(stream.unsealed for stream in streams),
        first_break=first_break,
        note=(
            "unsealed rows never passed through the decision desk (the "
            "?seed=1 demo verdicts are the intended case) and are reported "
            "rather than absorbed — the chain covers what the desk decided"
        ),
    )


@router.get("/verify")
def verify_audit_chain(
    conn: sqlite3.Connection = Depends(db.get_db),
) -> AuditVerifyReport:
    """Recompute the decision ledger's hash chain end to end.

    The append-only trail stops being a promise here: every sealed
    decision and lifecycle transition is re-hashed against the live row
    and against its predecessor, and the first mismatch is named.
    """
    return verify(conn)
