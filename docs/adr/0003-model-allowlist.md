# ADR 0003 — A model must be on an allowlist before it serves live traffic

**Status:** accepted · **Date:** 2026-08-02 · **Scope:** `app/llm.py`

## Context

Model names reach the live client as free text from the environment
(`SENTINEL_PANEL_MODELS`), and `DEFAULT_MODEL` is a constant that any
future edit can change. Both routes fail quietly, in two different ways.

A **typo** — `gemini-flash-lastest` — 404s on every call. The provider's
own failure handling turns that into an abstention, which is correct
behaviour and completely invisible: the panel convenes, nobody answers,
and the card records a review that never happened.

A **plausible name** is worse. On this project's billing-disabled key the
free tier grants pro models a quota of **zero** — measured against a real
key, not assumed. Swapping one in under jury-day pressure looks like an
upgrade and buys a seat that can never answer. The panel's whole claim is
that genuinely different models argued the same decision; a seat that
structurally cannot speak makes that claim false.

Neither failure is loud, and both produce a dashboard that overstates what
the system did. That is the specific harm worth engineering against.

## Decision

`ALLOWED_MODELS` in `app/llm.py` lists the models permitted to serve live
traffic. Membership means exactly one thing: **the free tier grants this
model non-zero quota, verified against a real key from this project.**

The gate is enforced at three points:

- `GeminiProvider.__init__` calls `assert_allowlisted` **before** building
  the client, so an unvetted model never holds a connection — including on
  the shared-client path the panel seats use.
- `get_provider` catches the refusal, logs it at ERROR, and returns the
  deterministic `FakeProvider`. Refusing a model must not take the product
  down, and it must not silently become a *different* model.
- `get_panel_providers` refuses **all seats or none**. A panel with one
  rejected seat answering from the fake composer while two ran live is not
  a heterogeneous panel; it is a rigged one, and the transcript would not
  say so.

`provider_mode()` — which `/health` reports — agrees with the gate, so the
deployment can never advertise a live backend the allowlist will refuse.

`panel_models()` still returns the roster **as configured**, allowlisted or
not. Filtering there would make rejected names disappear; the caller names
them in the log instead.

## Consequences

- Adding a model is a deliberate act: a line in `ALLOWED_MODELS` and a
  test, not an environment variable set at 23:00.
- The failure mode moves from "quietly degraded" to "loudly deterministic".
  A refused configuration serves the fake provider, and every surface that
  reports provenance (`/health`, the card's `source`, the AI usage ledger,
  the run receipt) says `fake` — which is true.
- `gemini-2.5-flash` stays on the list although it 404s for keys minted
  after the spike. It is still free-tier valid on older keys, and where it
  is gone it fails into the abstain path, which is the honest outcome. The
  allowlist answers "is this free", not "does this exist today".
- The list will go stale as Google retires families. That is why the three
  seats use `-latest` aliases where they can: the alias tracks the current
  generation without an allowlist edit.

## Alternatives rejected

- **Trust the environment.** This is what produced the failure modes above.
- **Filter silently, keep the good seats.** Produces a panel that reports
  three reviewers and ran one, which is the exact overstatement this whole
  codebase's honesty rules exist to prevent.
- **Probe the model at boot.** A live call per model per boot spends the
  scarce daily quota to learn something a four-line constant already knows,
  and it fails the deployment when the network hiccups.
