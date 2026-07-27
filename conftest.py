"""Repo-level pytest configuration.

Lives at the root so pytest's rootdir/import path covers the flat module
layout (main.py, models.py, detection.py) without packaging tricks.
"""

import os

import pytest


def pytest_sessionstart(session):
    """Never let a stray real key make the suite spend live Gemini quota."""
    os.environ.setdefault("SENTINEL_FAKE_LLM", "1")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Point every test at a throwaway database file.

    Keeps tests from touching a developer's local cloudsentinel.db and
    keeps them independent of each other. Tests that exercise the shared
    path read SENTINEL_DB_PATH exactly like production code does.
    """
    monkeypatch.setenv("SENTINEL_DB_PATH", str(tmp_path / "test.db"))
    # keep the suite hermetic: knobs exported in the developer's shell (the
    # natural way to demo TTL expiry or an alternate detector) must not
    # leak into tests that assert default behavior
    for env in (
        "SENTINEL_ACTION_TTL_HOURS",
        "SENTINEL_DETECTOR",
        "SENTINEL_BASELINE_WINDOW_DAYS",
        "SENTINEL_SEASONAL",
        "SENTINEL_PULSE_LLM_BUDGET",
        "SENTINEL_PULSE_RATE_LIMIT_PER_MINUTE",
        "SENTINEL_MONTHLY_BUDGET",
        "SENTINEL_LLM_TIMEOUT_SECONDS",
        "SENTINEL_ENV",
        "SENTINEL_REBASE_DATES",
        "SENTINEL_DEMO_RESET",
        "SENTINEL_READONLY",
        "SENTINEL_COSTS_SOURCE",
        "SENTINEL_COSTS_FILE",
        "SENTINEL_COSTS_FEED_URL",
        "SENTINEL_SECURITY_FEED_URL",
        "SENTINEL_FRAUD_FEED_URL",
        "SENTINEL_FEED_TTL_SECONDS",
        "SENTINEL_SELF_TELEMETRY",
        "SENTINEL_MISSION",
        "SENTINEL_PANEL_MODELS",
        "SENTINEL_LEAVE_ONE_OUT",
        "SENTINEL_LOGIN_RATE_LIMIT_PER_MINUTE",
        "SENTINEL_WATCH_INTERVAL_SECONDS",
    ):
        monkeypatch.delenv(env, raising=False)
    # live-data state is process-global; each test starts with a clean slate
    from app import feeds, telemetry

    feeds.reset_cache()
    telemetry.reset_buffer()
