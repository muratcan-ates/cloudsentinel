"""Signal enrichment — fast reads and the deterministic context gathering.

The context half of this suite has one governing claim to defend: every
signal is either derived from data in the repo or absent. So the tests
check the derivation against hand-computable series, and they check the
absences — an unknown service, a date outside the series, a missing
inventory file — return nothing rather than a plausible-sounding guess.
"""

from datetime import date, timedelta

import pytest

from app import detection, enrichment
from app.enrichment import (
    ATTACK_TECHNIQUES,
    FINOPS_CAPABILITIES,
    attack_technique,
    blast_radius_tier,
    context_lines,
    deployment_context,
    finops_capability,
    framework_reference,
    gather_context,
    ownership,
    usage_shape,
    verification_plan,
)


START = date(2026, 6, 20)


def series(service, costs):
    """Daily records for one service, one cost per consecutive day from START."""
    return [
        {
            "service": service,
            "date": (START + timedelta(days=i)).isoformat(),
            "cost": cost,
        }
        for i, cost in enumerate(costs)
    ]


def day(offset):
    return (START + timedelta(days=offset)).isoformat()


@pytest.fixture
def mock_records():
    return detection.load_mock_dataset()["daily_costs"]


# --- fast reads -------------------------------------------------------------


def test_blast_radius_tiers_scale_with_magnitude():
    assert blast_radius_tier(0.0).startswith("L0")
    assert blast_radius_tier(3.2).startswith("L1")
    assert blast_radius_tier(4.5).startswith("L2")
    assert blast_radius_tier(5.5).startswith("L3")


def test_blast_radius_is_sign_agnostic():
    assert blast_radius_tier(-6.0).startswith("L3")


def test_framework_reference_by_kind():
    assert framework_reference("cost_anomaly")["framework"] == "FinOps Framework"
    assert framework_reference("security_signal")["framework"] == "MITRE ATT&CK"
    assert framework_reference("fraud")["framework"] == "MITRE ATT&CK"


# --- ATT&CK: the security and fraud lanes -----------------------------------


def test_each_watched_surface_maps_to_its_own_technique():
    assert attack_technique("auth-gateway").id == "T1110"
    assert attack_technique("api-edge").id == "T1110.003"
    assert attack_technique("admin-portal").id == "T1078.004"


def test_an_unmapped_surface_falls_back_instead_of_inventing_an_id():
    """A plausible-looking wrong technique id is worse than the general one."""
    tag = attack_technique("some-service-we-never-modelled")
    assert tag.id == "T1110"
    assert tag.framework == "MITRE ATT&CK"


def test_the_fraud_lane_maps_to_impact():
    tag = attack_technique(fraud=True)
    assert tag.id == "T1657"
    assert tag.domain == "Impact"


def test_matching_is_case_and_whitespace_insensitive():
    assert attack_technique("  Auth-Gateway  ").id == "T1110"


@pytest.mark.parametrize("surface", sorted(ATTACK_TECHNIQUES))
def test_every_technique_link_follows_the_mitre_path_shape(surface):
    """A sub-technique nests under its parent: T1110.003 -> /T1110/003/."""
    tag = attack_technique(surface)
    assert tag.url == (
        "https://attack.mitre.org/techniques/"
        f"{tag.id.replace('.', '/')}/"
    )
    assert tag.url.startswith("https://attack.mitre.org/techniques/T")


def test_the_reference_line_carries_id_name_and_tactic():
    assert attack_technique("auth-gateway").reference == (
        "T1110 Brute Force — Credential Access"
    )


# --- FinOps: the cost lane ---------------------------------------------------


def test_each_card_category_maps_to_a_capability():
    assert finops_capability("RIGHTSIZING").name == "Workload Optimization"
    assert finops_capability("INVESTIGATION").name == "Anomaly Management"
    assert finops_capability("BUDGET_GUARD").name == "Budgeting"


def test_an_unknown_category_falls_back_to_anomaly_management():
    assert finops_capability("SOMETHING_NEW").name == "Anomaly Management"
    assert finops_capability(None).name == "Anomaly Management"


@pytest.mark.parametrize("category", sorted(FINOPS_CAPABILITIES))
def test_capabilities_have_no_id_and_link_the_published_index(category):
    """The framework names capabilities rather than numbering them."""
    tag = finops_capability(category)
    assert tag.id is None
    assert tag.url == "https://www.finops.org/framework/capabilities/"
    assert tag.framework == "FinOps Framework"


def test_framework_reference_narrows_with_the_extra_context():
    generic = framework_reference("security_signal")
    specific = framework_reference("security_signal", service="admin-portal")
    assert generic["id"] == "T1110"
    assert specific["id"] == "T1078.004"
    assert framework_reference("cost_anomaly", category="LIFECYCLE")["name"] == (
        "Architecting for Cloud"
    )


def test_verification_plan_names_the_service_and_saving():
    anomaly = {"service": "ec2", "service_mean": 120.0}
    savings = {"cautious_monthly": 428.0}
    plan = verification_plan(anomaly, savings)
    joined = " ".join(plan)
    assert "ec2" in joined
    assert "428" in joined
    assert any("simulated" in step for step in plan)


# --- usage shape ------------------------------------------------------------


def test_usage_shape_reads_a_one_day_blip():
    records = series("compute", [10, 10, 10, 10, 10, 10, 90, 10, 10, 10])
    shape = usage_shape(records, "compute", day(6))
    assert shape["baseline_median"] == 10.0
    assert shape["peak"] == 90.0
    assert shape["aftermath"] == "returned"
    assert "blip" in shape["summary"]


def test_usage_shape_reads_a_step_change():
    records = series("compute", [10, 10, 10, 10, 10, 10, 90, 88, 91, 89])
    shape = usage_shape(records, "compute", day(6))
    assert shape["aftermath"] == "still-elevated"
    assert "step change" in shape["summary"]


def test_usage_shape_flags_a_partial_return():
    records = series("compute", [10, 10, 10, 10, 10, 10, 90, 88, 10, 10])
    assert usage_shape(records, "compute", day(6))["aftermath"] == "partially-returned"


def test_usage_shape_does_not_call_a_collapse_a_spike():
    """Detection is sign-agnostic — a cost DROP flags too, and 'did it come
    back down' is the wrong question about one."""
    records = series("compute", [50, 50, 50, 50, 50, 50, 2, 50, 50, 50])
    shape = usage_shape(records, "compute", day(6))
    assert shape["aftermath"] == "not-elevated"
    assert "not a spend spike" in shape["summary"]


def test_usage_shape_reports_the_run_up_direction():
    rising = series("compute", [10, 11, 12, 14, 17, 20, 90])
    assert usage_shape(rising, "compute", day(6))["trend"] == "rising"
    falling = series("compute", [20, 17, 14, 12, 11, 10, 90])
    assert usage_shape(falling, "compute", day(6))["trend"] == "falling"


def test_usage_shape_refuses_to_read_a_trend_from_two_points():
    shape = usage_shape(series("compute", [10, 10, 90]), "compute", day(2))
    assert shape["trend"] == "unread"
    assert "unread" not in shape["summary"]


def test_usage_shape_window_is_bounded_by_the_days_argument():
    records = series("compute", [10] * 20 + [90])
    assert usage_shape(records, "compute", day(20), days=7)["days_observed"] == 7


def test_usage_shape_ignores_other_services():
    records = series("compute", [10, 10, 10, 10, 10, 10, 90]) + series(
        "storage", [500, 500, 500, 500, 500, 500, 500]
    )
    shape = usage_shape(records, "compute", day(6))
    assert shape["baseline_median"] == 10.0
    assert shape["peak"] == 90.0


def test_usage_shape_survives_malformed_records():
    records = series("compute", [10, 10, 10, 10, 10, 10, 90])
    records.append({"service": "compute", "date": day(7), "cost": "n/a"})
    records.append({"service": "compute"})
    assert usage_shape(records, "compute", day(6))["peak"] == 90.0


def test_usage_shape_absent_for_an_unknown_service_or_date():
    records = series("compute", [10, 10, 10])
    assert usage_shape(records, "database", day(1)) is None
    assert usage_shape(records, "compute", "1999-01-01") is None


# --- deployment markers -----------------------------------------------------


def test_deployment_marker_on_the_compute_spike():
    """The fixture's whole point: the 2026-06-29 compute spike has a deploy."""
    marker = deployment_context("compute", "2026-06-29")
    assert marker["coincides"] is True
    assert marker["deployment"]["ref"] == "rel-8814"
    assert "the same day" in marker["summary"]


def test_deployment_marker_reaches_one_day_back():
    """A change that lands late bills tomorrow, so the day before counts."""
    marker = deployment_context("network", "2026-07-01")
    assert marker["coincides"] is True
    assert marker["deployment"]["date"] == "2026-06-30"


def test_no_deployment_near_the_database_spike_is_itself_evidence():
    marker = deployment_context("database", "2026-07-02")
    assert marker["coincides"] is False
    assert marker["deployment"] is None
    assert "does not explain" in marker["summary"]


def test_deployment_after_the_signal_cannot_explain_it():
    """api-edge shipped on 2026-07-01; a signal the day BEFORE is not its fault."""
    assert deployment_context("api-edge", "2026-06-30")["coincides"] is False


def test_deployment_context_absent_outside_the_inventory():
    assert deployment_context("payments", "2026-07-02") is None


def test_deployment_context_ignores_an_unparseable_date():
    assert deployment_context("compute", "not-a-date") is None


# --- ownership --------------------------------------------------------------


def test_ownership_carries_team_rota_and_tags():
    owner = ownership("compute")
    assert owner["owner"] == "platform-engineering"
    assert owner["on_call"] == "platform-primary"
    assert owner["tags"]["cost_center"] == "CC-1042"
    assert "platform-engineering" in owner["summary"]


def test_ownership_surfaces_click_ops_resources():
    """managed_by is a different remediation conversation, so it must show."""
    assert ownership("network")["tags"]["managed_by"] == "console"
    assert "managed_by=console" in ownership("network")["summary"]


def test_ownership_absent_rather_than_guessed():
    assert ownership("payments") is None


def test_ownership_tags_are_a_copy_callers_cannot_poison():
    first = ownership("compute")
    first["tags"]["tier"] = "tampered"
    assert ownership("compute")["tags"]["tier"] == "critical"


# --- gathering --------------------------------------------------------------


def test_gather_context_collects_all_three_signals(mock_records):
    context = gather_context({"service": "compute", "date": "2026-06-29"}, mock_records)
    assert context["gathered"] == ["deployment", "ownership", "usage"]
    lines = context_lines(context)
    assert len(lines) == 3
    assert lines[0].startswith("Usage:")
    assert "rel-8814" in lines[1]


def test_gather_context_without_records_skips_the_usage_shape():
    context = gather_context({"service": "compute", "date": "2026-06-29"})
    assert "usage" not in context["gathered"]
    assert "ownership" in context["gathered"]


def test_gather_context_of_an_unknown_service_is_empty_not_invented(mock_records):
    context = gather_context({"service": "payments", "date": "2026-07-02"}, mock_records)
    assert context["gathered"] == []
    assert context_lines(context) == []


def test_gather_context_tolerates_a_shapeless_anomaly():
    assert gather_context({})["gathered"] == []
    assert gather_context({"service": None, "date": None})["gathered"] == []
    assert gather_context({"service": "compute"})["gathered"] == ["ownership"]


def test_gather_context_degrades_silently_without_the_inventory(
    monkeypatch, tmp_path, mock_records
):
    """A missing fixture costs the report two signals, never an exception."""
    monkeypatch.setattr(enrichment, "INVENTORY_FILE", tmp_path / "gone.json")
    context = gather_context({"service": "compute", "date": "2026-06-29"}, mock_records)
    assert context["gathered"] == ["usage"]


def test_gather_context_survives_a_corrupt_inventory(monkeypatch, tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json")
    monkeypatch.setattr(enrichment, "INVENTORY_FILE", broken)
    assert gather_context({"service": "compute", "date": "2026-06-29"})["gathered"] == []


# --- the fixture and the estate must agree ----------------------------------


def test_every_cost_service_is_attributed(mock_records):
    """An anomaly nobody owns is an anomaly nobody fixes."""
    for service in {record["service"] for record in mock_records}:
        assert ownership(service) is not None, service


def test_deployment_dates_follow_the_demo_rebase(monkeypatch):
    """SENTINEL_REBASE_DATES moves the cost data toward today; a change log
    left behind would silently stop coinciding with anything."""
    monkeypatch.setenv("SENTINEL_REBASE_DATES", "1")
    records = detection.load_mock_dataset()["daily_costs"]
    spike = max(
        (r for r in records if r["service"] == "compute"), key=lambda r: r["cost"]
    )
    marker = deployment_context("compute", spike["date"])
    assert marker["coincides"] is True
    assert marker["deployment"]["date"] == spike["date"]


def test_the_flagged_spikes_gather_context_end_to_end(mock_records):
    """Whatever detection flags, enrichment has something to say about it."""
    run = detection.run_detection(mock_records, threshold=2.0)
    assert run.anomalies
    for anomaly in run.anomalies:
        context = gather_context(anomaly.model_dump(), mock_records)
        assert context["gathered"] == ["deployment", "ownership", "usage"]
