"""Named uncertainty sources: derived facts, not model self-report.

The load-bearing property is that these lists are computed from the
evidence an agent actually had — so they are identical in the fake, live
and fallback lanes, and the deliberate 0.5 demo confidence stays exactly
where it was.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import analyst, debate, llm, recommender
from app.analyst import AnalystReport
from app.llm import Confidence
from main import app

client = TestClient(app)


def _codes(sources):
    return {source["code"] for source in sources}


def _report(evidence_ids=("E14",)):
    return AnalystReport(
        triage="REAL",
        summary="s",
        probable_cause="c",
        evidence_ids=list(evidence_ids),
        confidence=Confidence(score=0.5, rationale="r"),
    )


def _evidence(days):
    return [{"eid": f"E{i + 1}", "date": f"2026-07-{i + 1:02d}"} for i in range(days)]


# --- the vocabulary ---------------------------------------------------------


def test_every_code_the_agents_emit_has_a_label():
    """An unlabelled code is a typo, and a typo would read as a finding."""
    with pytest.raises(KeyError):
        llm.uncertainty("not_a_real_code", "detail")


def test_a_live_source_contributes_no_provider_uncertainty():
    assert llm.provider_uncertainty("gemini") == []
    assert _codes(llm.provider_uncertainty("fake")) == {"simulated_provider"}
    assert _codes(llm.provider_uncertainty("fallback")) == {"simulated_provider"}


# --- the analyst ------------------------------------------------------------


def test_a_full_window_with_a_clean_baseline_names_only_what_is_true():
    anomaly = {
        "service": "compute",
        "severity": "critical",
        "detector_params": {"leave_one_out": True, "seasonal": True},
    }
    sources = analyst.analyst_uncertainty(
        anomaly, _evidence(14), _report(), "gemini"
    )
    assert _codes(sources) == set()


def test_a_short_window_is_named():
    anomaly = {"service": "compute", "severity": "critical",
               "detector_params": {"leave_one_out": True, "seasonal": True}}
    sources = analyst.analyst_uncertainty(anomaly, _evidence(4), _report(), "gemini")
    assert _codes(sources) == {"short_baseline"}
    assert "4 of the 14-day window" in sources[0]["detail"]


def test_a_lone_evidence_row_is_named_twice_over():
    anomaly = {"service": "compute", "severity": "critical",
               "detector_params": {"leave_one_out": True, "seasonal": True}}
    sources = analyst.analyst_uncertainty(anomaly, _evidence(1), _report(), "gemini")
    assert _codes(sources) == {"short_baseline", "single_day_evidence"}


def test_an_uncited_narrative_is_named():
    anomaly = {"service": "compute", "severity": "critical",
               "detector_params": {"leave_one_out": True, "seasonal": True}}
    sources = analyst.analyst_uncertainty(
        anomaly, _evidence(14), _report(evidence_ids=()), "gemini"
    )
    assert _codes(sources) == {"no_evidence_cited"}


def test_a_baseline_that_contains_the_flagged_day_is_named():
    """The default detector leaves the anomaly inside its own baseline —
    the project's own standing critique, said out loud on the card."""
    anomaly = {"service": "compute", "severity": "critical", "detector_params": {}}
    sources = analyst.analyst_uncertainty(anomaly, _evidence(14), _report(), "gemini")
    assert "contaminated_baseline" in _codes(sources)


def test_a_warning_grade_signal_and_a_fake_provider_are_named():
    anomaly = {
        "service": "compute",
        "severity": "warning",
        "detector_params": {"leave_one_out": True, "seasonal": False},
    }
    sources = analyst.analyst_uncertainty(anomaly, _evidence(14), _report(), "fake")
    assert _codes(sources) == {
        "simulated_provider",
        "unseasoned_baseline",
        "warning_grade_signal",
    }


# --- the recommender --------------------------------------------------------


def _rec_sources(**overrides):
    kwargs = {
        "anomaly": {"service": "compute"},
        "analyst_report": {"triage": "REAL", "confidence": {"score": 0.9}},
        "savings": {"daily_excess": 120.0},
        "numeric_check": {"status": "ok", "figures": []},
        "memory_mix": {"approved": 3, "rejected": 0},
        "source": "gemini",
        "threshold": 0.6,
    }
    kwargs.update(overrides)
    return recommender.recommender_uncertainty(**kwargs)


def test_a_well_supported_proposal_names_nothing():
    assert _codes(_rec_sources()) == set()


def test_an_absent_precedent_is_named():
    assert _codes(_rec_sources(memory_mix={"approved": 0, "rejected": 0})) == {
        "no_decision_memory"
    }


def test_a_split_precedent_is_named():
    sources = _rec_sources(memory_mix={"approved": 2, "rejected": 1})
    assert _codes(sources) == {"contested_memory"}
    assert "2 approved, 1 rejected" in sources[0]["detail"]


def test_a_shaky_handover_is_named():
    sources = _rec_sources(
        analyst_report={"triage": "SEASONAL", "confidence": {"score": 0.3}}
    )
    assert _codes(sources) == {"low_upstream_confidence", "triage_disputes_premise"}


def test_unverified_money_in_the_narrative_is_named():
    sources = _rec_sources(
        numeric_check={"status": "flagged", "figures": [{"figure": "$9,000"}]}
    )
    assert _codes(sources) == {"unverified_figures"}


def test_a_drop_below_baseline_has_nothing_to_recover():
    """A cost COLLAPSE is a real signal with a zero savings projection —
    the card must not imply money is on the table."""
    assert _codes(_rec_sources(savings={"daily_excess": 0.0})) == {
        "no_measurable_excess"
    }


# --- the deliberation -------------------------------------------------------


def test_an_abstaining_seat_and_a_lost_quorum_are_named():
    reviewers = [
        {"persona": "stability", "stance": "CAUTIOUS", "source": "gemini"},
        {"persona": "throughput", "stance": None, "source": None},
        {"persona": "evidence", "stance": None, "source": None},
    ]
    sources = debate.panel_uncertainty(reviewers, answered=1)
    assert _codes(sources) == {"seat_abstained", "no_quorum"}
    assert "throughput, evidence" in sources[0]["detail"]


def test_a_full_live_panel_names_nothing():
    reviewers = [
        {"persona": persona, "stance": "CAUTIOUS", "source": "gemini"}
        for persona in ("stability", "throughput", "evidence")
    ]
    assert debate.panel_uncertainty(reviewers, answered=3) == []


def test_the_demo_panel_states_a_modest_confidence_and_says_why():
    """The deliberate 0.5 stays; it is not replaced by the new field."""
    verdict = debate._fake_panel_payload(
        {"persona": "stability", "draft_recommendation": {"preferred": "CAUTIOUS"}}
    )
    assert verdict["confidence"]["score"] == 0.5
    assert "no live model" in verdict["confidence"]["rationale"]


# --- what the API actually publishes ----------------------------------------


def _recommended_card():
    report = client.post("/pulse").json()
    event_id = report["chain"][0]["event_id"]
    return client.post(f"/anomalies/{event_id}/recommend").json()


def test_the_trace_carries_confidence_and_uncertainty_per_hop():
    body = _recommended_card()
    hops = {step["step"]: step for step in body["trace"]}
    assert hops["analyst"]["confidence"] == 0.5  # the demo constant, untouched
    assert isinstance(hops["analyst"]["uncertainty_sources"], list)
    assert hops["recommender"]["confidence"] is not None
    for step in ("analyst", "recommender"):
        for source in hops[step]["uncertainty_sources"]:
            assert set(source) == {"code", "label", "detail"}
            assert source["code"] in llm.UNCERTAINTY_LABELS


def test_the_demo_lane_names_its_own_simulation_on_every_agent():
    body = _recommended_card()
    hops = {step["step"]: step for step in body["trace"]}
    for step in ("analyst", "recommender"):
        assert "simulated_provider" in _codes(hops[step]["uncertainty_sources"])


def test_the_persisted_card_carries_the_same_trace():
    """The operator inbox reads detail_json, not the recommend response."""
    _recommended_card()
    actions = client.get("/actions").json()["actions"]
    trace = actions[0]["detail"]["trace"]
    analyst_hop = next(step for step in trace if step["step"] == "analyst")
    assert analyst_hop["uncertainty_sources"]
    assert analyst_hop["confidence"] == 0.5


def test_the_analysis_envelope_persists_its_uncertainty():
    report = client.post("/pulse").json()
    event_id = report["chain"][0]["event_id"]
    from app import db

    conn = db.connect_ready()
    try:
        row = conn.execute(
            "SELECT analysis_json FROM events WHERE id = ?", (event_id,)
        ).fetchone()
    finally:
        conn.close()
    envelope = json.loads(row["analysis_json"])
    assert envelope["uncertainty_sources"]
    assert envelope["report"]["confidence"]["score"] == 0.5


def test_an_envelope_from_before_the_field_existed_still_traces():
    """Persisted envelopes are replayed, so the trace must tolerate one
    that predates uncertainty entirely."""
    trace = recommender._assemble_trace(
        {"source": "fake", "model": "m", "reflected": False},
        {"source": "fake", "model": "m", "transcript": None},
        from_cache=True,
        memory_entry_count=0,
    )
    hops = {step["step"]: step for step in trace}
    assert hops["analyst"]["uncertainty_sources"] == []
    assert hops["analyst"]["confidence"] is None
