"""Signal enrichment — blast-radius tiers and framework references."""

from app.enrichment import (
    blast_radius_tier,
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


def test_verification_plan_names_the_service_and_saving():
    anomaly = {"service": "ec2", "service_mean": 120.0}
    savings = {"cautious_monthly": 428.0}
    plan = verification_plan(anomaly, savings)
    joined = " ".join(plan)
    assert "ec2" in joined
    assert "428" in joined
    assert any("simulated" in step for step in plan)
