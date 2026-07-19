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
