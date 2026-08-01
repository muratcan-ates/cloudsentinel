"""Tests for the mission DSL and the reflex engine core (Sprint 3, S3-①).

Acceptance criteria: the YAML is data (safe_load, hard validation, no
silent defaults), the reflex pass resolves mission > env > argument
precedence correctly and measures its own latency, and the learning
loop only ever SUGGESTS rules mined from decision memory.

The validation half of this file is written from the operator's side:
each case asserts not merely that a bad file is refused, but that the
refusal names the file, the key and the range — a loader that raises
without saying which of forty lines is wrong has only moved the search.
"""

import pytest
import yaml

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


def test_every_shipped_config_still_loads_unchanged():
    """The strict loader must not have changed what the configs MEAN.

    Value for value: what the schema produces has to equal what a plain
    safe_load of the same bytes produces — no coercion, no dropped key,
    no default quietly filling a hole. Every file in configs/ is checked,
    so a mission added later cannot skip the bar.
    """
    paths = sorted(missions.CONFIG_DIR.glob("*.yaml"))
    assert {path.stem for path in paths} >= {"finops", "security", "fraud"}
    for path in paths:
        raw = yaml.safe_load(path.read_text())
        loaded = load_mission(path.stem)
        assert loaded.mission == path.stem  # slug and filename agree
        assert loaded.model_dump(exclude_none=True) == raw


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


def _body_without(dotted: str) -> str:
    """The valid body with one key removed — a config with a hole in it."""
    import json

    body = json.loads(_valid_body())
    target = body
    *parents, leaf = dotted.split(".")
    for parent in parents:
        target = target[parent]
    del target[leaf]
    return json.dumps(body)


def _refusal(config_dir, body: str, name: str = "tuned") -> str:
    (config_dir / f"{name}.yaml").write_text(body)
    with pytest.raises(MissionError) as raised:
        load_mission(name)
    message = str(raised.value)
    assert f"{name}.yaml" in message  # every refusal names the file
    return message


def test_declared_name_must_match_filename(config_dir):
    (config_dir / "alias.yaml").write_text(_valid_body())  # declares "tuned"
    with pytest.raises(MissionError, match="declares mission"):
        load_mission("alias")


def test_declared_slug_must_itself_be_a_slug(config_dir):
    message = _refusal(config_dir, _valid_body(mission="../etc"))
    assert "mission — '../etc' does not match" in message


@pytest.mark.parametrize(
    "overrides, expected",
    [
        (
            {"detection.threshold": -1},
            "detection.threshold — accepts a number in (0.0, 100.0], got -1",
        ),
        (
            {"detection.critical_z": 500.0},
            "detection.critical_z — accepts a number in (0.0, 100.0], got 500.0",
        ),
        (
            {"detection.baseline_window_days": 3},
            "detection.baseline_window_days — accepts an integer in [7, 365], got 3",
        ),
        (
            {"detection.baseline_window_days": 4000},
            "detection.baseline_window_days — accepts an integer in [7, 365], got 4000",
        ),
        (
            {"escalation.confidence_debate_threshold": 1.5},
            "confidence_debate_threshold — accepts a number in [0.0, 1.0], got 1.5",
        ),
        (
            {"detection.source": "weather"},
            "detection.source — accepts one of: cost, security, fraud, got 'weather'",
        ),
        (
            {"detection.detector": "quantum"},
            "detection.detector — accepts one of: zscore, mad, got 'quantum'",
        ),
    ],
)
def test_out_of_range_values_name_the_key_and_the_accepted_range(
    config_dir, overrides, expected
):
    """A refusal that does not say what to fix is just a longer outage."""
    assert expected in _refusal(config_dir, _valid_body(**overrides))


@pytest.mark.parametrize(
    "overrides, expected",
    [
        # a lax parser would read all four of these as the value the
        # author only APPEARS to have written
        ({"detection.threshold": "2.0"}, "detection.threshold — accepts a number"),
        ({"detection.seasonal": "no"}, "detection.seasonal — accepts true or false"),
        (
            {"detection.baseline_window_days": 28.0},
            "detection.baseline_window_days — accepts an integer",
        ),
        ({"role_intent": {"analyst": 3}}, "role_intent.analyst — input should be"),
    ],
)
def test_types_are_strict_so_a_config_means_what_it_says(
    config_dir, overrides, expected
):
    assert expected in _refusal(config_dir, _valid_body(**overrides))


def test_unknown_keys_are_refused_not_ignored(config_dir):
    """leave_one_out is a real detector knob — of the ENVIRONMENT layer,
    not of the DSL. Tolerated here it would sit in the file looking live
    while no scan ever reads it, which nothing downstream can notice."""
    message = _refusal(config_dir, _valid_body(**{"detection.leave_one_out": True}))
    assert "detection.leave_one_out — unknown key" in message
    assert "detection accepts: source, threshold, critical_z" in message


def test_unknown_top_level_keys_are_refused(config_dir):
    message = _refusal(config_dir, _valid_body(colour="blue"))
    assert "colour — unknown key" in message
    assert "the mission accepts: mission, title, description" in message


def test_missing_keys_name_the_range_they_would_have_accepted(config_dir):
    message = _refusal(config_dir, _body_without("detection.detector"))
    assert "detection.detector — required, accepts one of: zscore, mad" in message


def test_blank_free_text_is_a_missing_value_in_disguise(config_dir):
    """Every free-text field reaches an agent prompt; a blank one drops
    the mission's intent out of that prompt without a sound."""
    message = _refusal(config_dir, _valid_body(organizational_intent="   "))
    assert "organizational_intent — must not be blank" in message


def test_role_intent_must_carry_at_least_one_role(config_dir):
    message = _refusal(config_dir, _valid_body(role_intent={}))
    assert "role_intent — accepts a mapping with 1 entry or more" in message


def test_critical_z_below_the_flagging_threshold_refuses_to_load(config_dir):
    """Ordering the DSL cannot express is checked here rather than felt
    later: under critical_z < threshold every flagged signal is critical
    and the warning band silently stops existing."""
    message = _refusal(config_dir, _valid_body(**{"detection.critical_z": 1.0}))
    assert "critical_z (1.0) must be at least threshold (2.0)" in message


def test_a_rules_block_outside_the_fraud_lane_refuses_to_load(config_dir):
    """Only app/fraud.py reads rules, and only from the fraud mission;
    anywhere else the block is decoration that changes nothing."""
    bands = {"hold_band": 70, "review_band": 40, "new_account_days": 30}
    message = _refusal(config_dir, _valid_body(rules=bands))
    assert "nothing would ever read it" in message


def test_a_fraud_mission_without_rules_refuses_to_load(config_dir):
    """The other half: a missing block hands scoring back to the code
    constants while the file looks like it is in charge."""
    message = _refusal(config_dir, _valid_body(**{"detection.source": "fraud"}))
    assert "must carry a rules block" in message


def test_every_fault_is_reported_in_one_pass(config_dir):
    """Two mistakes, one boot: fixing a config one exception per attempt
    is how a five-minute edit becomes an afternoon."""
    message = _refusal(
        config_dir,
        _valid_body(
            **{
                "detection.threshold": -1,
                "escalation.confidence_debate_threshold": 2,
            }
        ),
    )
    assert "detection.threshold" in message
    assert "escalation.confidence_debate_threshold" in message


_DUPLICATED_KEY_BODY = """
mission: tuned
title: t
description: d
organizational_intent: o
role_intent:
  analyst: a
detection:
  source: cost
  threshold: 2.0
  threshold: 9.0
  critical_z: 3.0
  detector: zscore
  baseline_window_days: 28
  seasonal: false
escalation:
  confidence_debate_threshold: 0.6
"""


def test_duplicate_keys_refuse_to_load(config_dir):
    """YAML keeps the last of two twins, so the line an operator reads
    would not be the setting in force. The loader will not take it."""
    message = _refusal(config_dir, _DUPLICATED_KEY_BODY)
    shadowed_line = _DUPLICATED_KEY_BODY.splitlines().index("  threshold: 9.0") + 1
    assert f"duplicate key 'threshold' on line {shadowed_line}" in message


@pytest.mark.parametrize("twin", ["tuned.yml", "Tuned.yaml", "TUNED.YAML"])
def test_a_slug_twin_refuses_to_load(config_dir, twin):
    """Only tuned.yaml is ever read. A .yml or case twin is a file you
    can edit with no effect — and on a case-insensitive filesystem it may
    be the one that loads, so the same repo behaves differently on a
    macOS laptop and on the Linux deployment."""
    (config_dir / twin).write_text(_valid_body())
    with pytest.raises(MissionError, match="shadowed by"):
        load_mission("tuned")


def test_a_twin_blocks_even_an_otherwise_valid_config(config_dir):
    (config_dir / "tuned.yaml").write_text(_valid_body())
    (config_dir / "tuned.yml").write_text(_valid_body())
    with pytest.raises(MissionError, match="shadowed by tuned.yml"):
        load_mission("tuned")


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
    # 50.0 mutes the lane on this data (max |z| is ~6) and stays inside the
    # loader's bounds: z is capped at 100 and critical_z may not sit below
    # the flagging threshold.
    (config_dir / "finops.yaml").write_text(
        _valid_body(
            mission="finops",
            **{"detection.threshold": 50.0, "detection.critical_z": 50.0},
        )
    )
    clear_mission_cache()
    quiet = client.get("/anomalies").json()
    assert quiet["threshold"] == 50.0  # resolved threshold reported honestly
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
    client.post(
        f"/actions/{first['action_id']}/reject",
        json={"actor": "op", "rationale": "free the reuse lane"},
    )

    (config_dir / "finops.yaml").write_text(
        _valid_body(mission="finops", **{"escalation.confidence_debate_threshold": 0.9})
    )
    clear_mission_cache()
    second = client.post(f"/anomalies/{event_id}/recommend").json()
    assert second["from_cache"] is False  # new threshold -> new cache partition


# --- quick-switch (in-memory override) ---------------------------------------


def test_quick_switch_resolution_order(monkeypatch):
    from app.missions import set_active_mission

    assert get_mission().mission == "finops"  # code default
    monkeypatch.setenv("SENTINEL_MISSION", "security")
    clear_mission_cache()
    assert get_mission().mission == "security"  # env boot default
    set_active_mission("fraud")
    assert get_mission().mission == "fraud"  # in-memory override outranks env
    assert get_mission("finops").mission == "finops"  # explicit arg always wins
    clear_mission_cache()  # the test hook also resets the override
    monkeypatch.delenv("SENTINEL_MISSION")
    assert get_mission().mission == "finops"


def test_set_active_mission_rejects_unknown_slugs():
    from app.missions import set_active_mission

    with pytest.raises(MissionError):
        set_active_mission("ghost")
    assert get_mission().mission == "finops"  # a failed flip changes nothing


def test_pulse_quick_switch_flips_every_following_surface(client):
    body = client.post("/pulse?mission=security").json()
    assert body["mission"] == "security"
    # the flip is LIVE: the dashboard's polling surface follows it too
    assert client.get("/anomalies").json()["mission"] == "security"


def test_pulse_unknown_mission_fails_loudly(client):
    response = client.post("/pulse?mission=ghost")
    assert response.status_code == 400
    assert "mission" in response.json()["detail"]
    assert client.get("/anomalies").json()["mission"] == "finops"  # unchanged


def test_pulse_malformed_mission_is_rejected_by_the_pattern(client):
    assert client.post("/pulse?mission=../etc").status_code == 422
