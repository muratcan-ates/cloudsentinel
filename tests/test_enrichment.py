"""Signal enrichment — blast-radius tiers and framework references."""

import pytest

from app.enrichment import (
    ATTACK_TECHNIQUES,
    FINOPS_CAPABILITIES,
    attack_technique,
    blast_radius_tier,
    finops_capability,
    framework_reference,
    verification_plan,
)


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
