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
