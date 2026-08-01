# Architecture Decision Records

Short records of the choices that a reader would otherwise have to guess
at — written so that a decision is legible **as a decision**, with its cost
named, rather than looking like something nobody got around to.

| # | Decision | Scope |
|---|---|---|
| [0001](0001-sqlite-as-the-system-of-record.md) | SQLite is the system of record — and the path out of it | `app/db.py` |
| [0002](0002-no-agent-framework.md) | The agent loop is written, not imported | `app/pulse.py`, the agents |
| [0003](0003-model-allowlist.md) | A model must be on an allowlist before it may answer live | `app/llm.py` |
| [0004](0004-execute-is-simulated-dispatch-is-real.md) | Execute is simulated; the dispatch that carries it is real | `app/actions.py`, `app/dispatch.py` |
| [0005](0005-the-fake-provider-is-a-first-class-lane.md) | The fake provider is a first-class lane, and the demo runs on it | `app/llm.py` |

Related: [`docs/architecture.md`](../architecture.md) for the system shape,
and [`SECURITY.md`](../../SECURITY.md) for the reporting policy and the
security posture these decisions add up to.
