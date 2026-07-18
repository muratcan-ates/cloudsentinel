# CloudSentinel — one-command workflows (pip + venv by locked decision).
# `make setup` once, then `make run`, `make test` or `make demo`.

PYTHON ?= python3
VENV := .venv
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff

.PHONY: setup run test demo smoke drill

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
		$(UVICORN) main:app --host 127.0.0.1 --port 8000

smoke:
	bash scripts/smoke.sh

drill:
	bash scripts/failure_drill.sh
