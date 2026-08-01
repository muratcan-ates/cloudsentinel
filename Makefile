# CloudSentinel — one-command workflows (pip + venv by locked decision).
# `make setup` once, then `make run`, `make test` or `make demo`.

PYTHON ?= python3
VENV := .venv
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff

.PHONY: setup run test demo demo-live demo-sim smoke drill

setup:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt -r requirements-dev.txt

run:
	$(UVICORN) main:app --host 127.0.0.1 --port 8000

test:
	$(RUFF) check .
	SENTINEL_FAKE_LLM=1 $(PYTEST) -q

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

smoke:
	bash scripts/smoke.sh

drill:
	bash scripts/failure_drill.sh
