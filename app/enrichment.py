"""Signal enrichment — the deterministic context gathered before agents speak.

Two kinds of help for an operator staring at a flagged signal.

The fast reads: how big the blow-up is (a discrete L0–L3 blast-radius tier
from the deviation magnitude), which industry framework it maps to (the
FinOps Framework for cost, MITRE ATT&CK for security/fraud), and how a
human confirms an approved fix actually worked.

The context: the three questions an operator asks next, in order — what
has this service been *doing* lately, did anything *ship* around then, and
*who owns it*. The usage shape is arithmetic over the same daily records
detection scored; the change log and the ownership tags come from a
committed inventory fixture (``data/service_inventory.json``) that covers
the same synthetic estate as the cost and security data. Nothing here
calls a model and nothing here fabricates a fact: an absent signal is
simply left out, because a plausible guess about who owns a service is
worse than no answer at all.

Every function degrades silently. Enrichment is additive — a missing
fixture, an unknown service or a date that is not in the series must
never take an incident report down with it.

The framework mapping below is a table, not a model call. That is the
point: an operator who already speaks ATT&CK or the FinOps Framework can
read our finding in their own vocabulary, and the answer is the same
every time because it was looked up rather than written. A technique we
cannot map honestly falls back to the lane's general entry instead of
inventing a plausible id.
"""
import json
import statistics
from datetime import date, timedelta
from pathlib import Path

from app.models import FrameworkTag
INVENTORY_FILE = Path(__file__).parent / "data" / "service_inventory.json"
# Days of history (the flagged day included) that make up the usage shape,
# and how far past it we look to see whether spend came back down.
USAGE_WINDOW_DAYS = 7
FOLLOW_DAYS = 3
# A following day still counts as elevated at half again the pre-spike
# baseline — well clear of the ±5% jitter the mock estate runs at, so
# "it came back down" means it really did.
ELEVATED_RATIO = 1.5
# Drift below this fraction over the pre-spike days is noise, not a trend.
TREND_BAND = 0.1
# A deployment can only explain a signal it preceded, and only barely:
# same day, or the day before (a change that lands late bills tomorrow).
DEPLOY_WINDOW_DAYS = 1
# (key, label) in the order an operator reads them.
CONTEXT_LABELS = (
    ("usage", "Usage"),
    ("deployment", "Change log"),
    ("ownership", "Ownership"),
)

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


def _days(count: int) -> str:
    return "1 day" if count == 1 else f"{count} days"


def _series(records: list, service: str) -> list[tuple[str, float]]:
    """One service's (date, cost) points, oldest first, garbage dropped.

    The caller hands over whatever the feed produced, malformed records
    included; a context signal is the last place that should raise on them.
    """
    points: list[tuple[str, float]] = []
    for record in records:
        if not isinstance(record, dict) or record.get("service") != service:
            continue
        try:
            points.append((str(record["date"]), float(record["cost"])))
        except (KeyError, TypeError, ValueError):
            continue
    points.sort()
    return points


def _trend(costs: list[float]) -> str:
    """Direction of the run-up, or an honest refusal when it is too short."""
    if len(costs) < 4:
        return "unread"
    half = len(costs) // 2
    early = statistics.mean(costs[:half])
    late = statistics.mean(costs[half:])
    if early == 0:
        return "unread"
    drift = (late - early) / early
    if drift > TREND_BAND:
        return "rising"
    if drift < -TREND_BAND:
        return "falling"
    return "steady"


def usage_shape(
    records: list,
    service: str,
    on_date: str,
    *,
    days: int = USAGE_WINDOW_DAYS,
    follow_days: int = FOLLOW_DAYS,
) -> dict | None:
    """What the service was doing around the flagged day, from the same records.

    Answers the question a cost number alone cannot: is this a one-day
    blip that already cleared, or a step change the estate is still paying
    for? The second needs an action today; the first often needs an
    explanation and nothing else — and telling them apart is arithmetic
    over the series detection already scored, not a judgement call.

    None when the service or the date is absent from the records — the
    caller then simply has no usage signal to show.
    """
    points = _series(records, service)
    index = next((i for i, (day, _) in enumerate(points) if day == on_date), None)
    if index is None:
        return None

    window = points[max(0, index - days + 1) : index + 1]
    before = [cost for _, cost in window[:-1]]
    after = points[index + 1 : index + 1 + follow_days]
    flagged = window[-1][1]
    baseline = round(statistics.median(before), 2) if before else None

    shape = {
        "service": service,
        "date": on_date,
        "days_observed": len(window),
        "daily": [{"date": day, "cost": cost} for day, cost in window],
        "baseline_median": baseline,
        "peak": round(max(cost for _, cost in window), 2),
        "trend": _trend(before),
        "aftermath": _aftermath(baseline, flagged, after),
        "days_after": len(after),
    }
    shape["summary"] = _usage_summary(shape, flagged)
    return shape


def _aftermath(
    baseline: float | None, flagged: float, after: list[tuple[str, float]]
) -> str:
    """Whether spend came back down in the days following the flagged one."""
    if baseline is None or baseline <= 0:
        return "unknown"
    # Detection is sign-agnostic, so a flagged day can be a collapse rather
    # than a spike; "did it come back down" is then the wrong question.
    if flagged < baseline * ELEVATED_RATIO:
        return "not-elevated"
    if not after:
        return "unknown"
    elevated = sum(1 for _, cost in after if cost >= baseline * ELEVATED_RATIO)
    if elevated == 0:
        return "returned"
    if elevated == len(after):
        return "still-elevated"
    return "partially-returned"


_AFTERMATH_PHRASE = {
    "returned": "spend came back to that baseline on the following days — a blip",
    "still-elevated": "spend has NOT come back down — this is a step change",
    "partially-returned": "spend has only partly come back down",
    "not-elevated": "the flagged day is not a spend spike against that baseline",
    "unknown": "no later days are recorded, so the aftermath is unknown",
}


def _usage_summary(shape: dict, flagged: float) -> str:
    """One sentence an operator can read without opening the series."""
    baseline = shape["baseline_median"]
    if baseline is None:
        return (
            f"{shape['service']} has no recorded history before "
            f"{shape['date']} — {flagged} is the first point."
        )
    trend = "" if shape["trend"] == "unread" else f" ({shape['trend']})"
    lead = (
        f"{shape['service']} ran near {baseline}/day over the "
        f"{_days(shape['days_observed'] - 1)} before {shape['date']}{trend}, "
        f"then {flagged}"
    )
    if baseline > 0:
        lead += f" ({round(flagged / baseline, 1)}x)"
    return f"{lead}; {_AFTERMATH_PHRASE[shape['aftermath']]}."


_inventory_cache: tuple[Path, int, dict] | None = None


def _demo_shift() -> timedelta:
    """How far the demo rebase moves the fixtures (zero when the knob is off).

    Lazy import on purpose: detection owns the rebase, and pulling the whole
    cost pipeline in at module scope would make this dependency-light module
    expensive for the callers that only want a blast-radius tier.
    """
    from app import detection

    try:
        return detection.demo_rebase_delta()
    except (OSError, ValueError, KeyError):
        return timedelta(0)


def _inventory() -> dict | None:
    """The committed inventory fixture, demo-rebased like the cost data.

    Cached on (path, shift) so the demo rebase — which moves the whole
    synthetic estate toward today — can never leave the change log pointing
    at dates the cost series no longer has. None when the file is missing or
    unreadable: no inventory means no ownership and no change log, which is
    a quieter report, not a broken one.
    """
    global _inventory_cache
    delta = _demo_shift()
    cached = _inventory_cache
    if cached is not None and cached[0] == INVENTORY_FILE and cached[1] == delta.days:
        return cached[2]
    try:
        with INVENTORY_FILE.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if delta:
        for entry in data.get("deployments", []):
            try:
                shipped = date.fromisoformat(str(entry["date"]))
            except (KeyError, TypeError, ValueError):
                continue
            entry["date"] = (shipped + delta).isoformat()
    _inventory_cache = (INVENTORY_FILE, delta.days, data)
    return data


def deployment_context(
    service: str, on_date: str, *, window_days: int = DEPLOY_WINDOW_DAYS
) -> dict | None:
    """Did anything ship into this service around the flagged day?

    The first question an on-call engineer asks about a spike, and the one
    the curated spend-spike runbook tells them to ask. A hit is a lead, not
    a verdict — and a miss is evidence too: no deployment means the cause
    is somewhere other than a change this estate made.

    None when the change log has no jurisdiction — an unreadable inventory,
    or a service it does not cover. Within its jurisdiction "nothing
    shipped" is an answer, and must not be confused with "we do not know".
    """
    inventory = _inventory()
    if inventory is None:
        return None
    if service not in (inventory.get("services") or {}):
        return None
    try:
        flagged = date.fromisoformat(str(on_date))
    except ValueError:
        return None

    nearest: tuple[int, dict] | None = None
    for entry in inventory.get("deployments", []):
        if not isinstance(entry, dict) or entry.get("service") != service:
            continue
        try:
            shipped = date.fromisoformat(str(entry.get("date")))
        except ValueError:
            continue
        gap = (flagged - shipped).days
        # Only deployments at or before the signal: a change that shipped
        # afterwards cannot have caused it, however close it looks.
        if 0 <= gap <= window_days and (nearest is None or gap < nearest[0]):
            nearest = (gap, entry)

    if nearest is None:
        span = "the same day" if window_days == 0 else f"the {_days(window_days)} before"
        return {
            "service": service,
            "window_days": window_days,
            "coincides": False,
            "deployment": None,
            "summary": (
                f"no deployment recorded for {service} in {span} {on_date} — "
                "the change log does not explain this one."
            ),
        }
    gap, entry = nearest
    when = "the same day" if gap == 0 else f"{_days(gap)} earlier"
    note = entry.get("note")
    return {
        "service": service,
        "window_days": window_days,
        "coincides": True,
        "deployment": dict(entry),
        "summary": (
            f"deployment {entry.get('ref', '(unlabelled)')} shipped to "
            f"{service} on {entry.get('date')}, {when}"
            + (f" — {note}." if note else ".")
        ),
    }


def ownership(service: str) -> dict | None:
    """Who to call, and what the resource is tagged as.

    An anomaly nobody owns is an anomaly nobody fixes, so the owning team,
    the on-call rota and the cost-centre tags travel with the signal. Note
    ``managed_by``: a resource changed by hand is a different remediation
    conversation from one a pipeline owns.

    None when the inventory does not know this service — an unattributed
    signal, honestly unattributed.
    """
    inventory = _inventory()
    if inventory is None:
        return None
    entry = (inventory.get("services") or {}).get(service)
    if not isinstance(entry, dict):
        return None
    tags = entry.get("tags") if isinstance(entry.get("tags"), dict) else {}
    owner = entry.get("owner") or "unattributed"
    environment = entry.get("environment") or "unknown environment"
    rendered = ", ".join(f"{key}={value}" for key, value in sorted(tags.items()))
    summary = f"{service} is owned by {owner} ({environment})"
    if entry.get("on_call"):
        summary += f", on-call {entry['on_call']}"
    if rendered:
        summary += f"; tags: {rendered}"
    return {
        "service": service,
        "owner": owner,
        "on_call": entry.get("on_call"),
        "environment": environment,
        "tags": dict(tags),
        "summary": summary + ".",
    }


def gather_context(anomaly: dict, records: list | None = None) -> dict:
    """The whole deterministic context for one signal, absent parts omitted.

    The "context is gathered" step of the product's chain, in one call:
    usage shape, change log, ownership. ``records`` is the caller's — the
    daily series detection already loaded — because loading it here could
    reach a live feed, and gathering context must stay pure and offline.

    ``gathered`` names which signals actually resolved, so a reader can
    tell a quiet estate from a thin one.
    """
    service = anomaly.get("service")
    on_date = anomaly.get("date")
    context: dict = {}
    if not isinstance(service, str) or not service:
        return {"gathered": []}

    if records and isinstance(on_date, str) and on_date:
        shape = usage_shape(records, service, on_date)
        if shape is not None:
            context["usage"] = shape
    if isinstance(on_date, str) and on_date:
        deployment = deployment_context(service, on_date)
        if deployment is not None:
            context["deployment"] = deployment
    owner = ownership(service)
    if owner is not None:
        context["ownership"] = owner

    gathered = sorted(context)
    context["gathered"] = gathered
    return context


def context_lines(context: dict) -> list[str]:
    """Render a gathered context as labelled lines for a report or a prompt."""
    lines = []
    for key, label in CONTEXT_LABELS:
        signal = context.get(key)
        if isinstance(signal, dict) and signal.get("summary"):
            lines.append(f"{label}: {signal['summary']}")
    return lines
