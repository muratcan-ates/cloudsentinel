"""Tests for the mission DSL and the reflex engine core (Sprint 3, S3-①).

Acceptance criteria: the YAML is data (safe_load, hard validation, no
silent defaults), the reflex pass resolves mission > env > argument
precedence correctly and measures its own latency, and the learning
loop only ever SUGGESTS rules mined from decision memory.
"""

import pytest

from app import db, missions
from app.benchmark import build_scenario
from app.missions import MissionError, clear_mission_cache, get_mission, load_mission
from app.reflex import reflex_scan, suggest_reflex_rules


@pytest.fixture(autouse=True)
def _fresh_mission_cache():
    clear_mission_cache()
    yield
    clear_mission_cache()


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(missions, "CONFIG_DIR", tmp_path)
    return tmp_path


# --- loading and validation -----------------------------------------------------


def test_finops_mission_loads_and_validates():
    mission = load_mission("finops")
    assert mission.mission == "finops"
    assert mission.detection.source == "cost"
    assert mission.detection.threshold == 2.0
    assert mission.detection.detector == "zscore"
    assert mission.detection.baseline_window_days == 28
    assert mission.escalation.confidence_debate_threshold == 0.6
    assert mission.organizational_intent.strip()
    assert {"analyst", "recommender", "operator"} <= set(mission.role_intent)


def test_get_mission_caches_until_cleared():
    first = get_mission("finops")
    assert get_mission("finops") is first
    clear_mission_cache()
    assert get_mission("finops") is not first


@pytest.mark.parametrize("name", ["../etc", "FinOps", "a/b", "", "x" * 65])
def test_mission_names_are_allow_listed(name):
    with pytest.raises(MissionError, match="invalid mission name"):
        load_mission(name)


def test_missing_mission_file_raises(config_dir):
    with pytest.raises(MissionError, match="not found"):
        load_mission("ghost")


def test_unparseable_yaml_raises(config_dir):
    (config_dir / "broken.yaml").write_text("mission: [unclosed")
    with pytest.raises(MissionError, match="unparseable"):
        load_mission("broken")


def test_non_mapping_yaml_raises(config_dir):
    (config_dir / "listy.yaml").write_text("- just\n- a\n- list\n")
    with pytest.raises(MissionError, match="mapping"):
        load_mission("listy")


def test_python_object_tags_are_rejected(config_dir):
    """safe_load treats config as data: a python-object tag must never
    construct anything — it fails the parse outright."""
    (config_dir / "evil.yaml").write_text(
        'mission: evil\npayload: !!python/object/apply:os.system ["echo pwned"]\n'
    )
    with pytest.raises(MissionError, match="unparseable"):
        load_mission("evil")


def _valid_body(**overrides) -> str:
    import copy
    import json

    body = {
        "mission": "tuned",
        "title": "t",
        "description": "d",
        "organizational_intent": "o",
        "role_intent": {"analyst": "a"},
        "detection": {
            "source": "cost",
            "threshold": 2.0,
            "critical_z": 3.0,
            "detector": "zscore",
            "baseline_window_days": 28,
            "seasonal": False,
        },
        "escalation": {"confidence_debate_threshold": 0.6},
    }
    merged = copy.deepcopy(body)
    for dotted, value in overrides.items():
        target = merged
        *parents, leaf = dotted.split(".")
        for parent in parents:
            target = target[parent]
        target[leaf] = value
    return json.dumps(merged)  # JSON is valid YAML


@pytest.mark.parametrize(
    "overrides",
    [
        {"detection.threshold": -1},
        {"detection.detector": "quantum"},
        {"detection.baseline_window_days": 3},
        {"detection.source": "weather"},
        {"escalation.confidence_debate_threshold": 1.5},
    ],
)
def test_schema_violations_refuse_to_load(config_dir, overrides):
    (config_dir / "tuned.yaml").write_text(_valid_body(**overrides))
    with pytest.raises(MissionError, match="mission tuned"):
        load_mission("tuned")


def test_declared_name_must_match_filename(config_dir):
    (config_dir / "alias.yaml").write_text(_valid_body())  # declares "tuned"
    with pytest.raises(MissionError, match="declares mission"):
        load_mission("alias")


# --- reflex engine --------------------------------------------------------------


def test_reflex_scan_uses_mission_defaults_and_measures_latency(monkeypatch):
    for env in ("SENTINEL_DETECTOR", "SENTINEL_BASELINE_WINDOW_DAYS", "SENTINEL_SEASONAL"):
        monkeypatch.delenv(env, raising=False)
    scenario = build_scenario("reflex", spikes=((10, 6.0),))
    result = reflex_scan(scenario.records, get_mission("finops"))
    assert result.mission == "finops"
    assert result.run.detector == "zscore"
    assert result.run.window_days == 28
    assert result.latency_ms > 0
    assert {(a.service, a.date) for a in result.run.anomalies} == scenario.planted


def test_env_override_beats_the_mission_file(monkeypatch):
    monkeypatch.setenv("SENTINEL_DETECTOR", "mad")
    scenario = build_scenario("reflex-env", spikes=((10, 6.0),))
    result = reflex_scan(scenario.records, get_mission("finops"))
    assert result.run.detector == "mad"


def test_explicit_threshold_beats_the_mission_default(monkeypatch):
    monkeypatch.delenv("SENTINEL_DETECTOR", raising=False)
    scenario = build_scenario("reflex-thr", spikes=((10, 6.0),))
    quiet = reflex_scan(scenario.records, get_mission("finops"), threshold=50.0)
    assert quiet.run.anomalies == []


# --- learning loop (suggestions only, HITL-sacred) ------------------------------


def seed_verdicts(service: str, approvals: int, rejections: int, age: str = "-1 days"):
    db.init_db()
    conn = db.connect()
    try:
        with db.writing(conn):
            for verdict, count in (("approved", approvals), ("rejected", rejections)):
                for _ in range(count):
                    conn.execute(
                        "INSERT INTO decisions (action_id, service, verdict, "
                        "rationale, input_context_json, created_at) "
                        "VALUES (NULL, ?, ?, NULL, '{}', datetime('now', ?))",
                        (service, verdict, age),
                    )
    finally:
        conn.close()


def test_suggestions_require_unanimous_approvals():
    seed_verdicts("unanimous", approvals=3, rejections=0)
    seed_verdicts("contested", approvals=3, rejections=1)
    seed_verdicts("thin", approvals=2, rejections=0)
    conn = db.connect()
    try:
        suggestions = suggest_reflex_rules(conn)
    finally:
        conn.close()
    assert [s["service"] for s in suggestions] == ["unanimous"]
    assert suggestions[0]["approvals"] == 3
    assert "consider" in suggestions[0]["suggestion"]  # advisory language only


def test_old_verdicts_fall_out_of_the_suggestion_window():
    seed_verdicts("ancient", approvals=5, rejections=0, age="-45 days")
    conn = db.connect()
    try:
        suggestions = suggest_reflex_rules(conn)
    finally:
        conn.close()
    assert suggestions == []


# --- API wiring -----------------------------------------------------------------


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as test_client:
        yield test_client


def test_anomaly_scan_reports_the_reflex_pass(client):
    body = client.get("/anomalies").json()
    assert body["mission"] == "finops"
    assert body["reflex_ms"] is not None
    assert body["reflex_ms"] > 0


def test_pulse_reports_mission_and_reflex_latency(client):
    body = client.post("/pulse").json()
    assert body["mission"] == "finops"
    assert body["reflex_ms"] is not None
    assert body["reflex_ms"] > 0
    assert body["signals"] >= 2  # the planted mock spikes still flow through


def test_reflex_suggestions_endpoint_is_advisory_only(client):
    seed_verdicts("unanimous", approvals=3, rejections=0)
    seed_verdicts("contested", approvals=3, rejections=1)
    body = client.get("/reflex/suggestions").json()
    assert body["count"] == 1
    assert body["suggestions"][0]["service"] == "unanimous"
    assert "operator" in body["note"]  # HITL-sacred, stated on the wire
    stricter = client.get("/reflex/suggestions", params={"min_approvals": 4}).json()
    assert stricter["count"] == 0


def test_reflex_suggestions_params_are_bounded(client):
    assert client.get("/reflex/suggestions", params={"window_days": 0}).status_code == 422
    assert client.get("/reflex/suggestions", params={"min_approvals": 1}).status_code == 422
    assert client.get("/reflex/suggestions", params={"window_days": 365}).status_code == 200


def test_debate_threshold_comes_from_the_mission_file(config_dir):
    from app.recommender import debate_threshold, escalation_trigger

    (config_dir / "finops.yaml").write_text(
        _valid_body(mission="finops", **{"escalation.confidence_debate_threshold": 0.9})
    )
    clear_mission_cache()
    assert debate_threshold() == 0.9
    assert escalation_trigger("REAL", 0.8) is not None  # 0.8 < 0.9 escalates
    assert "0.90" in escalation_trigger("REAL", 0.8)


def test_debate_threshold_falls_back_when_mission_is_unloadable(config_dir):
    from app.recommender import CONFIDENCE_DEBATE_THRESHOLD, debate_threshold, escalation_trigger

    clear_mission_cache()  # config_dir is empty -> MissionError inside
    assert debate_threshold() == CONFIDENCE_DEBATE_THRESHOLD
    assert escalation_trigger("REAL", 0.8) is None  # 0.8 >= fallback 0.6


def test_mission_settings_actually_govern_the_scan(config_dir):
    """Anti-vacuity: a mission whose settings DIFFER from the code defaults
    must be visible in the run — a reflex that never reads the mission
    cannot pass this."""
    (config_dir / "finops.yaml").write_text(
        _valid_body(
            mission="finops",
            **{"detection.detector": "mad", "detection.baseline_window_days": 14},
        )
    )
    clear_mission_cache()
    scenario = build_scenario("governed", spikes=((20, 6.0),))
    result = reflex_scan(scenario.records, get_mission("finops"))
    assert result.run.detector == "mad"
    assert result.run.window_days == 14


def test_mission_threshold_governs_when_the_query_param_is_omitted(client, config_dir):
    """The endpoint's threshold is optional: omitted, the mission file rules."""
    (config_dir / "finops.yaml").write_text(
        _valid_body(mission="finops", **{"detection.threshold": 999.0})
    )
    clear_mission_cache()
    quiet = client.get("/anomalies").json()
    assert quiet["threshold"] == 999.0  # resolved threshold reported honestly
    assert quiet["anomaly_count"] == 0
    explicit = client.get("/anomalies", params={"threshold": 2.0}).json()
    assert explicit["threshold"] == 2.0  # a caller-supplied value still wins
    assert explicit["anomaly_count"] == 2


def test_mission_critical_z_reclassifies_severity(config_dir):
    """critical_z is a live knob: raising it demotes a z=5 spike to warning."""
    (config_dir / "finops.yaml").write_text(
        _valid_body(mission="finops", **{"detection.critical_z": 10.0})
    )
    clear_mission_cache()
    scenario = build_scenario("calm", spikes=((20, 6.0),))
    result = reflex_scan(scenario.records, get_mission("finops"))
    assert result.run.anomalies  # still flagged...
    assert all(a.severity == "warning" for a in result.run.anomalies)  # ...not critical


def test_api_answers_even_when_the_mission_config_is_broken(client, config_dir):
    """The MissionError fallback is a wire-level guarantee, not a comment:
    with no loadable mission the demo-critical endpoints still answer."""
    clear_mission_cache()  # config_dir is empty
    scan = client.get("/anomalies")
    assert scan.status_code == 200
    body = scan.json()
    assert body["mission"] is None
    assert body["reflex_ms"] is None
    assert body["threshold"] == 2.0  # code default
    assert body["anomaly_count"] == 2
    pulse = client.post("/pulse")
    assert pulse.status_code == 200
    assert pulse.json()["mission"] is None


def test_invalid_env_override_falls_back_to_the_mission_not_code_defaults(
    config_dir, monkeypatch
):
    """Garbage in SENTINEL_DETECTOR must not silently veto the mission layer."""
    (config_dir / "finops.yaml").write_text(
        _valid_body(mission="finops", **{"detection.detector": "mad"})
    )
    clear_mission_cache()
    monkeypatch.setenv("SENTINEL_DETECTOR", "quantum")
    scenario = build_scenario("veto", spikes=((20, 6.0),))
    result = reflex_scan(scenario.records, get_mission("finops"))
    assert result.run.detector == "mad"  # mission survives the garbage env


def test_changed_debate_threshold_partitions_the_llm_cache(client, config_dir):
    """A cached recommendation replays its escalation decision, so tuning
    the mission threshold must produce a cache MISS, not a replay."""
    from tests.test_recommender import seed_analyzed_event

    (config_dir / "finops.yaml").write_text(_valid_body(mission="finops"))
    clear_mission_cache()
    event_id = seed_analyzed_event(service="compute", occurred_on="2026-07-01")
    first = client.post(f"/anomalies/{event_id}/recommend").json()
    client.post(f"/actions/{first['action_id']}/reject")  # free the reuse lane

    (config_dir / "finops.yaml").write_text(
        _valid_body(mission="finops", **{"escalation.confidence_debate_threshold": 0.9})
    )
    clear_mission_cache()
    second = client.post(f"/anomalies/{event_id}/recommend").json()
    assert second["from_cache"] is False  # new threshold -> new cache partition
