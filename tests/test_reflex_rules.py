"""The neuroplasticity loop: settled decisions become rule DRAFTS.

The property under test is as much what the loop refuses to do — adopt,
activate, or paper over disagreement — as what it produces.
"""

import json

from fastapi.testclient import TestClient

from app import db, history, reflex
from main import app

client = TestClient(app)


def _context(service="compute", severity="critical", z_score=4.2, stance="CAUTIOUS"):
    return {
        "category": "rightsizing",
        "preferred": stance,
        "options": [
            {"stance": "CAUTIOUS", "title": "right-size the idle tier"},
            {"stance": "BOLD", "title": "decommission the tier"},
        ],
        "anomaly": {
            "service": service,
            "severity": severity,
            "z_score": z_score,
            "cost": 900.0,
        },
    }


def _decision(conn, verdict="approved", rationale="owner confirmed", **kwargs):
    """One decided card, trail included, exactly as the desk would leave it."""
    context = json.dumps(_context(**kwargs))
    with db.writing(conn):
        cursor = conn.execute(
            "INSERT INTO actions (event_id, title, detail_json) VALUES (NULL, ?, ?)",
            ("right-size the idle tier", context),
        )
        action_id = cursor.lastrowid
        history.record(conn, action_id, "filed", "agent:recommender")
        conn.execute(
            "INSERT INTO decisions "
            "(action_id, service, verdict, rationale, input_context_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (action_id, kwargs.get("service", "compute"), verdict, rationale, context),
        )
        history.record(conn, action_id, verdict, "operator:murat", rationale)
    return action_id


def test_three_settled_approvals_draft_a_rule():
    conn = db.connect_ready()
    try:
        for _ in range(3):
            _decision(conn)
        rules, contested = reflex.propose_reflex_rules(conn)
        assert contested == 0
        assert len(rules) == 1
        rule = rules[0]
        assert rule["signature"] == {
            "service": "compute",
            "severity": "critical",
            "direction": "spike",
            "category": "rightsizing",
        }
        assert rule["approvals"] == 3
        assert rule["rejections"] == 0
        assert len(rule["decision_ids"]) == 3
        assert rule["stance"] == "CAUTIOUS"
        assert "right-size the idle tier" in rule["proposed_action"]
    finally:
        conn.close()


def test_two_approvals_are_not_enough():
    conn = db.connect_ready()
    try:
        for _ in range(2):
            _decision(conn)
        rules, contested = reflex.propose_reflex_rules(conn)
        assert rules == []
        assert contested == 0
    finally:
        conn.close()


def test_the_threshold_is_the_weakest_signal_the_humans_approved():
    """A rule must not fire below anything an operator actually saw."""
    conn = db.connect_ready()
    try:
        for z_score in (4.2, 3.1, 6.8):
            _decision(conn, z_score=z_score)
        rules, _ = reflex.propose_reflex_rules(conn)
        assert rules[0]["threshold"] == 3.1
        assert "|z| ≥ 3.1" in rules[0]["condition"]
    finally:
        conn.close()


def test_a_single_rejection_contests_the_signature():
    conn = db.connect_ready()
    try:
        for _ in range(3):
            _decision(conn)
        _decision(conn, verdict="rejected", rationale="planned migration window")
        rules, contested = reflex.propose_reflex_rules(conn)
        assert rules == []
        assert contested == 1
    finally:
        conn.close()


def test_a_split_stance_contests_the_signature():
    """Same signature, same verdict, different remedy — unsettled."""
    conn = db.connect_ready()
    try:
        for _ in range(3):
            _decision(conn, stance="CAUTIOUS")
        _decision(conn, stance="BOLD")
        rules, contested = reflex.propose_reflex_rules(conn)
        assert rules == []
        assert contested == 1
    finally:
        conn.close()


def test_signatures_do_not_bleed_into_each_other():
    """Severity, direction and service each split the bucket."""
    conn = db.connect_ready()
    try:
        for _ in range(3):
            _decision(conn, service="compute", severity="critical")
        for _ in range(2):
            _decision(conn, service="compute", severity="warning")
        for _ in range(3):
            _decision(conn, service="storage", severity="critical", z_score=-5.0)
        rules, _ = reflex.propose_reflex_rules(conn)
        assert len(rules) == 2
        assert {
            (r["signature"]["service"], r["signature"]["direction"]) for r in rules
        } == {("compute", "spike"), ("storage", "drop")}
    finally:
        conn.close()


def test_seeded_verdicts_carry_no_signature_and_draft_nothing():
    conn = db.connect_ready()
    try:
        with db.writing(conn):
            for _ in range(5):
                conn.execute(
                    "INSERT INTO decisions "
                    "(action_id, service, verdict, rationale, input_context_json) "
                    "VALUES (NULL, 'compute', 'approved', 'seeded', "
                    "'{\"origin\": \"seeded demo verdict\"}')"
                )
        rules, contested = reflex.propose_reflex_rules(conn)
        assert rules == []
        assert contested == 0
    finally:
        conn.close()


def test_corrupt_context_is_skipped_not_fatal():
    conn = db.connect_ready()
    try:
        for _ in range(3):
            _decision(conn)
        with db.writing(conn):
            conn.execute(
                "INSERT INTO decisions "
                "(action_id, service, verdict, input_context_json) "
                "VALUES (NULL, 'compute', 'approved', 'not json at all')"
            )
        rules, _ = reflex.propose_reflex_rules(conn)
        assert len(rules) == 1
    finally:
        conn.close()


def test_the_window_forgets_old_verdicts():
    conn = db.connect_ready()
    try:
        for _ in range(3):
            _decision(conn)
        with db.writing(conn):
            conn.execute(
                "UPDATE decisions SET created_at = datetime('now', '-90 days')"
            )
        assert reflex.propose_reflex_rules(conn, window_days=30)[0] == []
        assert len(reflex.propose_reflex_rules(conn, window_days=180)[0]) == 1
    finally:
        conn.close()


def test_the_draft_reports_what_the_deliberation_cost():
    """The trail is what makes the saving concrete: this many hours of
    human attention went into a pattern nobody ever decided differently."""
    conn = db.connect_ready()
    try:
        action_ids = [_decision(conn) for _ in range(3)]
        placeholders = ",".join("?" for _ in action_ids)
        with db.writing(conn):
            conn.execute(
                "UPDATE action_events SET created_at = datetime(created_at, '-4 hours') "
                f"WHERE transition = 'filed' AND action_id IN ({placeholders})",
                action_ids,
            )
        rules, _ = reflex.propose_reflex_rules(conn)
        assert rules[0]["median_deliberation_hours"] == 4.0
    finally:
        conn.close()


def test_an_open_card_has_no_deliberation_to_report():
    conn = db.connect_ready()
    try:
        with db.writing(conn):
            cursor = conn.execute(
                "INSERT INTO actions (event_id, title, detail_json) "
                "VALUES (NULL, 'open card', '{}')"
            )
            history.record(conn, cursor.lastrowid, "filed", "agent:recommender")
            action_id = cursor.lastrowid
        assert history.deliberation_hours(conn, [action_id]) == {}
    finally:
        conn.close()


def test_the_endpoint_publishes_drafts_and_never_activates_them():
    conn = db.connect_ready()
    try:
        for _ in range(3):
            _decision(conn)
    finally:
        conn.close()
    body = client.get("/reflex/suggestions").json()
    assert body["contested_signatures"] == 0
    assert len(body["proposed_rules"]) == 1
    rule = body["proposed_rules"][0]
    assert rule["status"] == "proposed"
    assert "never enacts" in rule["activation"]
    assert rule["median_deliberation_hours"] is not None


def test_the_endpoint_publishes_the_contested_count():
    """Disagreement is reported, not quietly dropped."""
    conn = db.connect_ready()
    try:
        for _ in range(3):
            _decision(conn)
        _decision(conn, verdict="rejected", rationale="planned migration")
    finally:
        conn.close()
    body = client.get("/reflex/suggestions").json()
    assert body["proposed_rules"] == []
    assert body["contested_signatures"] == 1
