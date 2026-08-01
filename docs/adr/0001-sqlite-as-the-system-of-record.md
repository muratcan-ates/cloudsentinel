# ADR 0001 — SQLite is the system of record

**Status:** accepted · **Date:** 2026-08-02 · **Scope:** `app/db.py`

## Context

CloudSentinel persists events, actions, operator decisions, the append-only
lifecycle trail, the audit hash chain, the LLM cache and the AI usage
ledger. Every one of those is a durable record an auditor may want to read
back months later, which is a database's job and not a file's.

The obvious "serious" answer is PostgreSQL. The obvious pressure to reach
for it is that SQLite reads as a toy to a reviewer skimming a repo.

Two facts about this deployment decide it instead. First, the target's
filesystem is **ephemeral**: the disk is gone on every restart, so any
store must be able to rebuild its schema from nothing at boot. Second,
there is exactly one process, no horizontal scaling, and a working set
measured in thousands of rows.

## Decision

SQLite via the standard library, with a locked pragma set:

- WAL journal with `synchronous=NORMAL` and a 5 s busy timeout, so
  FastAPI's worker threads do not hit "database is locked" under normal
  contention.
- Every writing transaction opens `BEGIN IMMEDIATE`, taking the write lock
  up front instead of failing mid-transaction on upgrade.
- Connections are opened per use rather than shared across threads, so
  transactions can never interleave.
- `PRAGMA foreign_keys=ON` — referential integrity between
  actions/decisions/events is enforced by the storage layer, not by caller
  discipline.
- `init_db()` is idempotent and runs at every startup, because the disk
  will be empty again.
- Never call the network or the LLM inside an open transaction: it would
  hold the write lock for the length of the call.

Idempotency uses `INSERT … ON CONFLICT DO NOTHING RETURNING`, which is
race-safe and needs SQLite ≥ 3.35.

## Consequences

- Zero operational surface: no connection string, no migration service, no
  second container. `make setup` on a clean clone produces a working
  system, which is what a reviewer will actually do.
- The pragma set is not decoration. Without WAL and `BEGIN IMMEDIATE` the
  concurrency story would be genuinely bad, and the honest version of this
  ADR would read differently.
- **The real cost:** ephemeral storage means the deployed showcase forgets
  its decisions on restart. That is a deployment property, not a data-model
  one, and it is stated as a limitation rather than hidden.
- Single writer. Nothing in the design assumes more, and the moment
  something does, this decision is wrong.

## The path out

The exit is deliberately kept short, and the constraints above are exactly
what make it short:

1. All SQL lives behind `app/db.py` and the module-level query helpers; no
   ORM idioms to unpick.
2. The schema is plain DDL in one tuple, and every table is created by
   `CREATE TABLE IF NOT EXISTS`.
3. `writing()` is the only write path, so transaction semantics move as one
   unit.

Migrating means swapping the connection factory and the four SQLite-specific
statements (`ON CONFLICT … RETURNING`, `datetime('now', …)`, `julianday`,
`COLLATE NOCASE`) for their Postgres equivalents. It does **not** mean
re-architecting the persistence layer — and it was not done for the
competition build because a second service would add operational risk to
the one thing that must not break on demo day: the live link.

## Alternatives rejected

- **PostgreSQL + Alembic now.** Buys nothing this deployment can use and
  adds a service that can be down while the jury is watching.
- **JSON files on disk.** Loses transactions, loses referential integrity,
  and would make the audit chain's guarantees unenforceable.
- **In-memory only.** The audit trail is the product. It has to survive.
