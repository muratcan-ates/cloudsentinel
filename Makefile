# CloudSentinel — one-command workflows (pip + venv by locked decision).
# `make setup` once, then `make run`, `make test` or `make demo`.

PYTHON ?= python3
VENV := .venv
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
BANDIT := $(VENV)/bin/bandit
PIP_AUDIT := $(VENV)/bin/pip-audit

.PHONY: setup run test coverage audit demo demo-live demo-sim demo-proof smoke drill verify

setup:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt -r requirements-dev.txt

# The deployed showcase runs with the simulated stream on, so the plain
# local run does too — otherwise the tape is dark here and alive there,
# and every screenshot taken locally quietly disagrees with the product.
# Nothing is hidden by this: the payload carries simulated: true and the
# edition line reads SIMULATED LIVE.
run:
	SENTINEL_SIM_STREAM=1 SENTINEL_COSTS_SOURCE=sim \
		$(UVICORN) main:app --host 127.0.0.1 --port 8000

test:
	$(RUFF) check .
	SENTINEL_FAKE_LLM=1 $(PYTEST) -q

# Line coverage over the application, the figure quoted in the README.
coverage:
	SENTINEL_FAKE_LLM=1 $(PYTEST) -q --cov=app --cov=main --cov-report=term-missing

# The security product scans itself: bandit over our source (config and
# justified skips in bandit.yaml), pip-audit over the dependencies that
# actually ship. Both gate CI.
audit:
	$(BANDIT) -c bandit.yaml -r app main.py scripts -q
	$(PIP_AUDIT) -r requirements.txt --progress-spinner off
	$(PIP_AUDIT) -r requirements-dev.txt --progress-spinner off

# Fresh demo stage: fake provider (no quota), dates rebased to this week,
# demo reset armed. Run `make smoke` from another shell once it is up.
demo:
	SENTINEL_FAKE_LLM=1 SENTINEL_REBASE_DATES=1 SENTINEL_DEMO_RESET=1 \
		SENTINEL_SIM_STREAM=1 \
		$(UVICORN) main:app --host 127.0.0.1 --port 8000

# Synthetic-live stage: the cost lane rides the simulated stream — today is
# projected from the live run-rate, so a spike becomes a genuine detector
# signal while the camera rolls. Security/fraud stay on the rebased mock.
demo-sim:
	SENTINEL_FAKE_LLM=1 SENTINEL_REBASE_DATES=1 SENTINEL_DEMO_RESET=1 \
		SENTINEL_SIM_STREAM=1 SENTINEL_COSTS_SOURCE=sim \
		$(UVICORN) main:app --host 127.0.0.1 --port 8000

# Live-data stage: the cost lane serves the app's own request telemetry
# (SENTINEL_COSTS_SOURCE=self) — real traffic, accumulating while it runs.
demo-live:
	SENTINEL_FAKE_LLM=1 SENTINEL_COSTS_SOURCE=self SENTINEL_DEMO_RESET=1 \
		SENTINEL_SIM_STREAM=1 \
		$(UVICORN) main:app --host 127.0.0.1 --port 8000

# Closing beat of the demo: the suite regrouped into named guarantees, one
# readable PASS/FAIL line each. Non-zero if any guarantee breaks.
demo-proof:
	bash scripts/demo_proof.sh

smoke:
	bash scripts/smoke.sh

drill:
	bash scripts/failure_drill.sh

# Release gate: measure the counters the docs claim, follow every relative
# link, and — with a URL — confirm the live host is still serving our app.
#   make verify                 # local checkout only
#   make verify BASE=https://<host>
verify:
	bash scripts/verify_release.sh $(BASE)
