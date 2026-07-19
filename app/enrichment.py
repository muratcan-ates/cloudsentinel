"""Signal enrichment — blast-radius tiering and framework references.

Pure, deterministic helpers that give an operator two fast reads on a signal:
how big the blow-up is (a discrete L0–L3 blast-radius tier from the deviation
magnitude) and which industry framework it maps to (the FinOps Framework for
cost, MITRE ATT&CK for security/fraud). References, not classifications — a
recognizable anchor, computed, never generated.
"""


def blast_radius_tier(z_score: float) -> str:
    """Discrete L0–L3 severity from the deviation magnitude (sign-agnostic)."""
    magnitude = abs(float(z_score))
    if magnitude >= 5:
        return "L3 — severe"
    if magnitude >= 4:
        return "L2 — high"
    if magnitude >= 3:
        return "L1 — elevated"
    return "L0 — contained"


def framework_reference(kind: str) -> dict:
    """Map a signal kind to a recognized industry framework reference."""
    if kind and ("security" in kind or "fraud" in kind):
        return {
            "framework": "MITRE ATT&CK",
            "reference": "Impact — anomalous activity",
        }
    return {
        "framework": "FinOps Framework",
        "reference": "Anomaly Management capability",
    }


def verification_plan(anomaly: dict, savings: dict) -> list[str]:
    """How a human confirms an approved action actually resolved the signal.

    Deterministic, evidence-first: what to re-measure, the expected direction,
    and the saving that should follow. Execution is simulated in the
    competition build, so this is the plan production would run to close the
    detect-to-resolution loop against real post-change data.
    """
    service = anomaly.get("service", "the service")
    baseline = anomaly.get("service_mean", "its baseline")
    steps = [
        f"Re-measure {service}'s daily cost for 7 days after the change.",
        f"Confirm it returns toward its baseline (~{baseline}).",
    ]
    monthly = savings.get("cautious_monthly")
    if monthly:
        steps.append(f"Expected saving if resolved: ~${monthly}/month (cautious).")
    steps.append(
        "Execution is simulated here; in production this step re-checks the "
        "real post-change cost and links the result to the audit record."
    )
    return steps
