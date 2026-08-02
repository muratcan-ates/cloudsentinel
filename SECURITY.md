# Security Policy

CloudSentinel is a human-in-the-loop decision desk for cloud cost, security
and fraud signals. It holds an audit trail of operator decisions, so its
security posture is part of the product rather than a wrapper around it.

## Reporting a vulnerability

**Please do not open a public issue for a security report.**

Use either channel:

1. **GitHub private advisory** — the repository's *Security* tab →
   *Report a vulnerability*. Preferred: it keeps the report and the fix in
   one place.
2. **Email** — `muratcn.ates@gmail.com`, subject line starting
   `[cloudsentinel-security]`.

Useful in a report: the affected endpoint or module, the version or commit,
what an attacker gains, and the smallest reproduction you have. A `curl`
line beats a paragraph.

**What to expect:** acknowledgement within 5 days, an assessment with a fix
or an explicit "won't fix, and here is why" within 30 days. This is a
student competition project maintained by a small team, not a funded
security programme — that timeline is a good-faith commitment, not an SLA.
Please give us a chance to respond before disclosing publicly; we will
credit you in the fix unless you would rather we did not.

## Scope

**In scope** — this repository and the deployment it describes:

- the FastAPI application in `app/` and `main.py`
- authentication, session handling and role enforcement (`app/auth.py`)
- the HITL decision endpoints and their idempotency and state machine
- the audit hash chain and the append-only trail (`app/ledger.py`,
  `app/history.py`)
- the outbound target guard (`app/netguard.py`) and webhook dispatch
- prompt-injection handling at the agent boundary (`app/llm.py`)
- the static dashboard and its CSP

**Out of scope:**

- **`cloudsentinel.onrender.com` is not ours.** That hostname belongs to an
  unrelated app. Our deployment answers on `cloudsentinel-y5zh.onrender.com`.
  Please do not test the other host, and reports against it cannot be acted on.
- Findings that require an attacker to already hold the deployment's
  environment (the API key, `SENTINEL_*` variables, or the database file).
  Those are the trust boundary, not a bypass of it — with one deliberate
  exception, below.
- Third-party infrastructure: Render, Google Gemini, GitHub.
- Volumetric denial of service against a free-tier host.
- Missing hardening that is documented as a known limitation below or in
  [`docs/adr/`](docs/adr/README.md). We would still like to hear that our
  reasoning is wrong — send it as an ordinary issue.

**The deliberate exception:** database-level tampering *is* in scope, in one
direction. `GET /audit/verify` claims that editing `decisions` or
`action_events` behind the application's back is detectable. A way to alter
a sealed decision and still have that endpoint report `ok` is a real finding,
even though it presumes file access.

## Security posture

What is actually implemented, so a report can aim at the gaps rather than
rediscover the defences:

| Area | Posture |
|---|---|
| Identity | Local accounts, salted PBKDF2 (stdlib), opaque session tokens, four roles (viewer < analyst < approver < admin). Login is rate-limited. |
| Decision integrity | Every verdict and lifecycle transition is sealed into a SHA-256 hash chain inside the writing transaction; `GET /audit/verify` recomputes it against the live rows. |
| Prompt injection | All model-facing data is wrapped in untrusted-data delimiters, and every system instruction states that delimited content is data, not commands. Money figures are computed outside the model and post-checked against the narrative. |
| Outbound requests | Feed and webhook targets pass `netguard`: no loopback, link-local, private ranges or cloud metadata addresses. A webhook URL may embed a secret, so only its host is ever logged or stored. |
| Model selection | Allowlist-gated before a client is built ([ADR 0003](docs/adr/0003-model-allowlist.md)); a refused model degrades to the deterministic provider, loudly. |
| Infrastructure mutation | Simulated by design ([ADR 0004](docs/adr/0004-execute-is-simulated-dispatch-is-real.md)). The app holds no cloud credentials with write scope. |
| Write protection | `SENTINEL_READONLY=1` rejects every mutating method — the showcase deployment's posture. `SENTINEL_ENV=production` refuses to boot on a demo posture at all. |
| Browser surface | CSP with `script-src 'self'` and `frame-ancestors 'none'`, `nosniff`, `DENY` framing, `no-referrer`. CORS names an explicit origin list. |
| Data | Synthetic fixtures or the app's own request telemetry. No real customer data, no PII beyond demo account names. |
| Secrets | `.env` is git-ignored; the API key is never logged, never persisted, and never returned by an endpoint. Only prompt **hashes** reach the AI usage ledger. |
| Supply chain | Runtime dependencies are deliberately few and frozen. `make audit` runs `bandit` over our own source and `pip-audit` over the dependency set — the security product scans itself. |

## Known limitations

Stated here so they are decisions rather than discoveries:

- **Ephemeral storage.** The deployment's disk resets on restart, so the
  audit trail does not survive a redeploy. The schema rebuilds itself at
  boot by design ([ADR 0001](docs/adr/0001-sqlite-as-the-system-of-record.md)).
- **No multi-tenancy.** One estate, one trust boundary. There is no tenant
  isolation to bypass because there are no tenants.
- **Roles cannot be reached through the API.** `POST /auth/register` always
  creates a `viewer` regardless of the role in the body — the field is
  accepted, validated and discarded. The only path to `approver` or `admin`
  is the bootstrap pair `SENTINEL_ADMIN_USER` / `SENTINEL_ADMIN_PASSWORD`
  applied at startup. That closes the elevation hole an earlier build had,
  and it means a live-ops deployment currently has exactly one account that
  can approve; a real deployment would add an admin-gated elevation endpoint.
- **`POST /ops/demo-reset` wipes decision state**, including the audit
  chain. It is inert unless `SENTINEL_DEMO_RESET=1`, and answers 404
  otherwise — indistinguishable from not existing.
- **The demo runs on the deterministic provider**
  ([ADR 0005](docs/adr/0005-the-fake-provider-is-a-first-class-lane.md)),
  labelled as such on every surface that reports provenance.

The reasoning behind each of these lives in the
[ADR set](docs/adr/README.md).

## Supported versions

`main` is the only supported branch. Tagged competition submissions are
snapshots and do not receive backported fixes.
