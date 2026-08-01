"""Signal enrichment — blast-radius tiering and framework references.

Pure, deterministic helpers that give an operator two fast reads on a signal:
how big the blow-up is (a discrete L0–L3 blast-radius tier from the deviation
magnitude) and which industry framework it maps to (the FinOps Framework for
cost, MITRE ATT&CK for security/fraud). References, not classifications — a
recognizable anchor, computed, never generated.

The mapping is a table, not a model call. That is the point: an operator
who already speaks ATT&CK or the FinOps Framework can read our finding in
their own vocabulary, and the answer is the same every time because it was
looked up rather than written. A technique we cannot map honestly falls
back to the lane's general entry instead of inventing a plausible id.
"""

from app.models import FrameworkTag

MITRE = "MITRE ATT&CK"
FINOPS = "FinOps Framework"

# MITRE ATT&CK Enterprise techniques, keyed by the surface the security lane
# watches. The mock lane counts failed logins, so every entry sits in the
# credential/account family — the surface refines which one applies.
ATTACK_TECHNIQUES: dict[str, tuple[str, str, str]] = {
    # id, technique name, tactic
    "auth-gateway": ("T1110", "Brute Force", "Credential Access"),
    "api-edge": ("T1110.003", "Password Spraying", "Credential Access"),
    "admin-portal": (
        "T1078.004",
        "Valid Accounts: Cloud Accounts",
        "Privilege Escalation",
    ),
}
DEFAULT_ATTACK = ("T1110", "Brute Force", "Credential Access")
FRAUD_ATTACK = ("T1657", "Financial Theft", "Impact")

# FinOps Framework capabilities, keyed by the category the card carries.
FINOPS_CAPABILITIES: dict[str, tuple[str, str]] = {
    # capability, domain
    "RIGHTSIZING": ("Workload Optimization", "Optimize Usage & Cost"),
    "LIFECYCLE": ("Architecting for Cloud", "Optimize Usage & Cost"),
    "CONFIG_REVIEW": ("Cloud Policy & Governance", "Manage the FinOps Practice"),
    "INVESTIGATION": ("Anomaly Management", "Understand Usage & Cost"),
    "BUDGET_GUARD": ("Budgeting", "Quantify Business Value"),
}
DEFAULT_FINOPS = ("Anomaly Management", "Understand Usage & Cost")

# Per-technique deep links are a stable URL shape at MITRE; the FinOps
# Framework's capability slugs are not, so that side links the capability
# index rather than risking a 404 in front of a jury.
FINOPS_URL = "https://www.finops.org/framework/capabilities/"


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


def _attack_url(technique_id: str) -> str:
    """attack.mitre.org path: a sub-technique nests under its parent."""
    return f"https://attack.mitre.org/techniques/{technique_id.replace('.', '/')}/"


def attack_technique(service: str | None = None, *, fraud: bool = False) -> FrameworkTag:
    """The ATT&CK technique this security (or fraud) surface maps to."""
    if fraud:
        technique_id, name, tactic = FRAUD_ATTACK
    else:
        technique_id, name, tactic = ATTACK_TECHNIQUES.get(
            (service or "").strip().lower(), DEFAULT_ATTACK
        )
    return FrameworkTag(
        framework=MITRE,
        id=technique_id,
        name=name,
        domain=tactic,
        url=_attack_url(technique_id),
        reference=f"{technique_id} {name} — {tactic}",
    )


def finops_capability(category: str | None = None) -> FrameworkTag:
    """The FinOps Framework capability a cost card's category maps to."""
    capability, domain = FINOPS_CAPABILITIES.get(
        (category or "").strip().upper(), DEFAULT_FINOPS
    )
    return FrameworkTag(
        framework=FINOPS,
        id=None,
        name=capability,
        domain=domain,
        url=FINOPS_URL,
        reference=f"{capability} — {domain}",
    )


def framework_reference(
    kind: str, *, service: str | None = None, category: str | None = None
) -> dict:
    """Map a signal to its framework reference, as a plain dict.

    Kind picks the framework; service (security) or category (cost) picks
    the specific entry. Called bare it still answers — the lane's general
    reference — so every caller gets something recognizable.
    """
    if kind and ("security" in kind or "fraud" in kind):
        tag = attack_technique(service, fraud="fraud" in kind)
    else:
        tag = finops_capability(category)
    return tag.model_dump()


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
