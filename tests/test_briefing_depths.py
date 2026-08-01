"""The chronicler tells one run at three depths.

An executive, a manager and an engineer need different things from the
same pulse: exposure and whether anyone must act; the queue and where it
stands; the mechanics. One narration call produces all three, so the depth
split costs no extra quota, and on the fake lane every depth is computed
from the run's facts rather than generated — which is what lets these
tests assert the words.

The governing claim is that the three never contradict each other: they
are three readings of one fact dictionary, not three opinions.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import chronicler, db
from app.chronicler import rule_based_briefing, rule_based_depths
from main import app

BUSY = {
    "cost_signals": 2,
    "security_signals": 2,
    "fraud_flagged": 1,
    "cross_lane_cards": 1,
    "analyzed": 2,
    "proposals_filed": 2,
    "proposals_reused": 0,
    "top_service": "compute",
    "top_z_score": 3.61,
}
QUIET = {
    "cost_signals": 0,
    "security_signals": 0,
    "fraud_flagged": 0,
    "cross_lane_cards": 0,
    "analyzed": 0,
    "proposals_filed": 0,
    "proposals_reused": 0,
    "top_service": None,
    "top_z_score": None,
}


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# --- the three altitudes -----------------------------------------------------


def test_every_depth_is_written():
    depths = rule_based_depths(BUSY)
    assert depths.executive and depths.manager and depths.engineer
    assert len({depths.executive, depths.manager, depths.engineer}) == 3


def test_the_executive_line_carries_no_jargon():
    """A board reader gets exposure and a call to act, not metric names."""
    executive = rule_based_depths(BUSY).executive.lower()
    for jargon in ("z-score", "|z|", "z ", "detector", "baseline", "mad", "pulse"):
        assert jargon not in executive
    # what it MUST say: someone has to act, and nothing moved on its own
    assert "waiting" in executive
    assert "nothing was changed automatically" in executive


def test_the_manager_line_carries_the_queue():
    manager = rule_based_depths(BUSY).manager
    assert "analyzed 2 of 5" in manager
    assert "filed 2" in manager
    assert "reused 0" in manager
    assert "cross-lane card" in manager
    assert "compute" in manager


def test_the_engineer_line_carries_the_mechanics():
    engineer = rule_based_depths(BUSY).engineer
    assert "cost 2 / security 2 / fraud 1" in engineer
    assert "analyzed 2" in engineer
    assert "strongest |z| 3.61 on compute" in engineer


def test_no_depth_invents_a_figure():
    """Every number in every depth must appear in the facts it came from."""
    import re

    depths = rule_based_depths(BUSY)
    allowed = {"2", "5", "1", "0", "3", "3.61"}  # facts + their honest sums
    for text in (depths.executive, depths.manager, depths.engineer):
        for figure in re.findall(r"\d+(?:\.\d+)?", text):
            assert figure in allowed, f"{figure!r} is not in the facts: {text}"


def test_a_quiet_run_says_so_at_every_depth():
    depths = rule_based_depths(QUIET)
    assert "nothing deviated" in depths.executive
    assert "No single service stands out" in depths.manager
    assert "cost 0 / security 0 / fraud 0" in depths.engineer
    # a quiet engineer line has nothing to name
    assert "strongest" not in depths.engineer


def test_the_depths_agree_with_the_headline():
    """One run, one story — the depths cannot outrank the summary line."""
    report = rule_based_briefing(BUSY)
    assert "compute" in report.watch_next
    assert "compute" in report.depths.manager
    assert "compute" in report.depths.engineer


def test_identical_facts_produce_identical_depths():
    assert rule_based_depths(BUSY) == rule_based_depths(dict(BUSY))


def test_missing_facts_degrade_instead_of_raising():
    """A caller that hands over half a dictionary still gets a briefing."""
    depths = rule_based_depths({})
    assert depths.executive and depths.manager and depths.engineer
    depths = rule_based_depths({"top_service": "compute", "top_z_score": "n/a"})
    # a non-numeric z is dropped rather than printed as garbage
    assert "|z|" not in depths.engineer
    assert "compute" in depths.engineer


# --- through the pulse -------------------------------------------------------


def test_the_pulse_response_carries_all_three_depths(client):
    briefing = client.post("/pulse").json()["briefing"]
    assert briefing["depths"]["executive"]
    assert briefing["depths"]["manager"]
    assert briefing["depths"]["engineer"]


def test_the_depths_survive_a_dry_budget(client):
    """Zero budget lands on the deterministic fallback — still three depths."""
    briefing = client.post("/pulse", params={"llm_budget": 0}).json()["briefing"]
    assert briefing["source"] == "fallback"
    assert briefing["depths"]["engineer"].startswith("cost ")


def test_the_depths_survive_the_cache(client):
    conn = db.connect()
    try:
        first = chronicler.write_briefing(conn, BUSY)
        second = chronicler.write_briefing(conn, BUSY)
    finally:
        conn.close()
    assert second["from_cache"] is True
    assert second["depths"] == first["depths"]


def test_a_cache_row_that_predates_the_depths_is_a_miss_not_a_500(client):
    """A long-lived dev database must not brick the pulse on a schema change."""
    conn = db.connect()
    try:
        chronicler.write_briefing(conn, BUSY)
        # rewrite the stored envelope in the shape it had before depths
        with db.writing(conn):
            row = conn.execute(
                "SELECT key, response_json FROM llm_cache ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            envelope = json.loads(row["response_json"])
            envelope["report"].pop("depths")
            conn.execute(
                "UPDATE llm_cache SET response_json = ? WHERE key = ?",
                (json.dumps(envelope), row["key"]),
            )

        replay = chronicler.write_briefing(conn, BUSY)
    finally:
        conn.close()

    assert replay["from_cache"] is False
    assert replay["depths"]["engineer"]
