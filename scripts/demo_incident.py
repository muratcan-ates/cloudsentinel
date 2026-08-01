"""Write the live demo incident into the dataset contract the product reads.

The jury's fair question about any demo is "was that card baked in?".  This
script exists to answer it on camera: it *generates* the incident while
everyone watches, writes it to a file **outside** the repository, and the app
picks it up through the documented credential-free lane —

    SENTINEL_COSTS_FILE=<path>      the cost lane's imported-export lane
                                    (app/feeds.py::read_costs_file, the same
                                     entry point scripts/import_costs.py feeds)

— so nothing downstream is special-cased for the demo.  The detector scores
the numbers written here, the analyst triages that event, and the
recommender's money figures are computed from this baseline and this spike
by app/recommender.py::estimated_savings.  Change ``--spike`` and every
number downstream changes with it; that is the whole point.

The incident
------------
``payment-api`` runs at about $420/day for four weeks and then bills about
$693 on the newest day (+65%).  On that same day the security lane sees a
burst of public storage-object reads against the same service — one root
cause (a bucket left publicly readable), two lanes, and the operator gets to
join them.  The security payload is written too, but note the honest caveat
below: the security lane has **no file lane** in this codebase.

Determinism
-----------
No clock inside the numbers, no network, no unseeded PRNG.  Day-to-day
jitter is a fixed symmetric palette, shuffled per service by
``random.Random(--seed)``, so the same seed always writes byte-identical
costs.  Only the *dates* follow the calendar (the newest day defaults to
yesterday so the jury sees this week); pin them with ``--end-date`` for a
byte-identical file across days.  Every run prints a SHA-256 of what it
wrote so "same input" is checkable, not asserted.

Usage
-----
    .venv/bin/python scripts/demo_incident.py --healthy   # opening frame
    .venv/bin/python scripts/demo_incident.py             # the incident
    .venv/bin/python scripts/demo_incident.py --spike 900 # a bigger one
    .venv/bin/python scripts/demo_incident.py --end-date 2026-08-01 --quiet

``--healthy`` writes the same four weeks with the newest day left on its
baseline: same app, same file path, nothing flagged. Running it first and
the incident second is what makes the signal impossible to pre-bake.

The presenter's beat-by-beat page is docs/DEMO_SCENARIO.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_DIR = Path(tempfile.gettempdir()) / "cloudsentinel-demo"
COSTS_NAME = "incident_costs.json"
SECURITY_NAME = "incident_security.json"

# Four weeks: the detector's default rolling window is 28 days, so this is
# exactly the history it will actually score. More days would be written and
# then clipped by app/detection.py::_window_cutoff, which makes the printed
# baseline disagree with the one on screen — the worst possible demo bug.
DAYS = 28

INCIDENT_SERVICE = "payment-api"
INCIDENT_BASELINE = 420.0
DEFAULT_SPIKE = 693.0

# (service, baseline USD/day). The neighbours exist so the estate is an
# estate — and so "only one thing flagged" is a visible result rather than an
# empty chart.
#
# They are deliberately flat, with no weekly rhythm. The first draft gave the
# customer-facing services a weekend dip and the self-check below caught it
# immediately: the demo mission runs seasonal=false (configs/finops.yaml), so
# a flat baseline pools weekdays and weekends into one bimodal group, a
# weekend day that also draws the palette's most negative jitter lands at
# |z| > 2, and the estate grows a second anomaly nobody planned. Seasonality
# is a real feature here — app/data/seasonal_costs.json is the fixture that
# demonstrates it — but it is not this demo's story, and a stray warning card
# would cost more on camera than the realism buys.
ESTATE: tuple[tuple[str, float], ...] = (
    (INCIDENT_SERVICE, INCIDENT_BASELINE),
    ("checkout-web", 312.0),
    ("ledger-db", 268.0),
    ("object-store", 154.0),
    ("edge-cdn", 96.0),
)

# Day-to-day jitter as a balanced palette rather than random draws, for the
# reason scripts/make_seasonal_fixture.py already learned the hard way:
# Gaussian noise produces 2-sigma outliers *by definition*, so a seeded
# normal would reliably grow accidental anomalies and the planted one would
# stop being the story. Twelve entries, summing to zero, largest |member|
# 2.4% — and twelve is coprime with the seven-day week, so the pattern does
# not lock onto a weekday. The seed only ever picks the rotation, and over
# all twelve rotations the largest |z| a quiet service can reach is 1.69,
# comfortably under the 2.0 threshold: the estate stays quiet by
# construction, for every seed, not by luck with the one that was tried.
JITTER: tuple[float, ...] = (
    -0.024, 0.011, -0.006, 0.019, -0.015, 0.004,
    0.024, -0.011, 0.006, -0.019, 0.015, -0.004,
)

# The security lane counts one thing per service per day. Here that thing is
# public reads of storage objects — the surface the incident actually
# exposes, not another failed-login story.
SECURITY_METRIC = "public_storage_access_count"
SECURITY_ESTATE: tuple[tuple[str, int], ...] = (
    (INCIDENT_SERVICE, 3),
    ("object-store", 12),
    ("edge-cdn", 5),
)
DEFAULT_SECURITY_SPIKE = 47
# Small integer palette, same discipline: symmetric, sums to zero.
SECURITY_JITTER: tuple[int, ...] = (0, 1, -1, 0, -1, 1, 1, 0, -1, 0, -1, 1)

CURRENCY = "USD"
PROVIDER_NOTE = "AWS (synthetic — generated by scripts/demo_incident.py)"


def _dates(end: date, days: int) -> list[date]:
    return [end - timedelta(days=days - 1 - i) for i in range(days)]


def _rotated(palette, service: str, seed: int):
    """A deterministic per-service rotation of the jitter palette.

    Rotation, not shuffle: it keeps the palette's sum-zero and bounded-range
    properties exactly intact while still giving each service its own
    sequence, so the "no accidental second anomaly" guarantee survives every
    seed rather than only the one that was tested.
    """
    rng = random.Random(f"{seed}:{service}")  # noqa: S311 — fixture jitter, not crypto
    offset = rng.randrange(len(palette))
    return palette[offset:] + palette[:offset]


def build_costs(end: date, spike: float | None, seed: int) -> dict:
    """The cost dataset, in app/data/mock_costs.json's exact contract.

    ``spike=None`` writes the healthy estate — the same four weeks with the
    newest day left on its baseline. That is the demo's opening frame: same
    app, same file path, nothing flagged, so the signal that arrives one
    command later cannot have been sitting in the fixture.
    """
    days = _dates(end, DAYS)
    records: list[dict] = []
    for service, baseline in ESTATE:
        palette = _rotated(JITTER, service, seed)
        for index, day in enumerate(days):
            cost = baseline * (1.0 + palette[index % len(palette)])
            if service == INCIDENT_SERVICE and index == len(days) - 1:
                if spike is not None:
                    cost = spike  # the incident, stated exactly, not jittered
            records.append(
                {
                    "date": day.isoformat(),
                    "service": service,
                    "cost": round(cost, 2),
                }
            )
    records.sort(key=lambda r: (r["date"], r["service"]))
    headline = (
        f"{INCIDENT_SERVICE} runs at ~${INCIDENT_BASELINE:.0f}/day and bills "
        f"${spike:,.2f} on {days[-1].isoformat()}."
        if spike is not None
        else (
            f"Healthy estate — {INCIDENT_SERVICE} holds ~"
            f"${INCIDENT_BASELINE:.0f}/day through {days[-1].isoformat()}, "
            "no incident."
        )
    )
    return {
        "description": (
            f"CloudSentinel live demo incident — {PROVIDER_NOTE}. {headline} "
            "Generated for the demo and served through SENTINEL_COSTS_FILE; "
            "not a real billing export."
        ),
        "currency": CURRENCY,
        "period": {"start": days[0].isoformat(), "end": days[-1].isoformat()},
        "daily_costs": records,
    }


def build_security(end: date, spike: int | None, seed: int) -> dict:
    """The security dataset, in app/data/mock_security_events.json's contract.

    ``spike=None`` writes the quiet lane, for the same reason as above.
    """
    days = _dates(end, DAYS)
    rows: list[dict] = []
    for service, baseline in SECURITY_ESTATE:
        palette = _rotated(SECURITY_JITTER, service, seed)
        step = max(1, round(baseline * 0.12))
        for index, day in enumerate(days):
            count = max(0, baseline + step * palette[index % len(palette)])
            if (
                service == INCIDENT_SERVICE
                and index == len(days) - 1
                and spike is not None
            ):
                count = spike
            rows.append(
                {"service": service, "date": day.isoformat(), "count": int(count)}
            )
    rows.sort(key=lambda r: (r["date"], r["service"]))
    return {
        "metric": SECURITY_METRIC,
        "period": {"start": days[0].isoformat(), "end": days[-1].isoformat()},
        "daily_counts": rows,
    }


def _write(path: Path, payload: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _series(dataset: dict, service: str) -> list[dict]:
    return [r for r in dataset["daily_costs"] if r["service"] == service]


def score(dataset: dict) -> tuple[list, str] | tuple[None, str]:
    """Run the product's own detector over what we just wrote.

    Imported here, not reimplemented: if this preview and the dashboard ever
    disagree, the demo is over. A missing venv degrades to a note instead of
    a traceback in front of a camera.
    """
    try:
        from app.detection import DEFAULT_THRESHOLD, run_detection
    except Exception as exc:  # pragma: no cover - operator-facing degradation
        return None, f"detector preview unavailable ({exc.__class__.__name__}: {exc})"
    run = run_detection(dataset["daily_costs"], DEFAULT_THRESHOLD)
    return run.anomalies, f"threshold {DEFAULT_THRESHOLD} · detector {run.detector}"


def savings_for(anomaly) -> dict | None:
    """The recommender's own money maths over the detector's own numbers."""
    try:
        from app.recommender import estimated_savings
    except Exception:  # pragma: no cover - operator-facing degradation
        return None
    return estimated_savings(
        {"cost": anomaly.cost, "service_mean": anomaly.service_mean}
    )


def report(
    costs: dict,
    security: dict,
    costs_path: Path,
    security_path: Path,
    costs_digest: str,
    security_digest: str,
    port: int,
    healthy: bool,
) -> int:
    """Print what was written, loudly enough to read aloud. Returns exit code."""
    series = _series(costs, INCIDENT_SERVICE)
    history = [r["cost"] for r in series[:-1]]
    spike_row = series[-1]
    prior_mean = statistics.mean(history)
    rise = (spike_row["cost"] / prior_mean - 1.0) * 100.0

    print(
        "CloudSentinel — healthy estate written"
        if healthy
        else "CloudSentinel — demo incident written"
    )
    print("=" * 72)
    print(f"  cost dataset      {costs_path}")
    print(f"    sha256[:16]     {costs_digest}")
    print(f"  security dataset  {security_path}")
    print(f"    sha256[:16]     {security_digest}")
    print(
        f"  window            {costs['period']['start']} → {costs['period']['end']}"
        f"  ({DAYS} days, {len(ESTATE)} services, {len(costs['daily_costs'])} rows)"
    )
    print()
    label = "THE ESTATE" if healthy else "THE INCIDENT"
    print(f"  {label} — {INCIDENT_SERVICE} ({PROVIDER_NOTE.split(' ')[0]})")
    print(f"    baseline, {len(history)} prior days   ${prior_mean:,.2f}/day")
    print(f"    newest day {spike_row['date']}      ${spike_row['cost']:,.2f}")
    print(f"    rise                          {rise:+.1f}%")
    print("    last five days                "
          + ", ".join(f"{r['date'][5:]} ${r['cost']:,.2f}" for r in series[-5:]))
    print()

    sec_rows = [r for r in security["daily_counts"] if r["service"] == INCIDENT_SERVICE]
    sec_history = [r["count"] for r in sec_rows[:-1]]
    print(f"  SAME SERVICE, SECURITY LANE — {SECURITY_METRIC}")
    print(
        f"    baseline                      "
        f"{statistics.mean(sec_history):.1f} reads/day"
    )
    print(
        f"    newest day {sec_rows[-1]['date']}      "
        f"{sec_rows[-1]['count']} reads"
        + ("" if healthy else "  (public objects served)")
    )
    print()

    anomalies, note = score(costs)
    exit_code = 0
    if anomalies is None:
        print(f"  DETECTOR PREVIEW  {note}")
    elif healthy:
        print(f"  WHAT THE DETECTOR WILL SAY  ({note})")
        if anomalies:
            print(
                "    "
                + ", ".join(f"{a.service} z={a.z_score}" for a in anomalies)
                + "  <-- UNEXPECTED: the healthy estate must flag nothing."
            )
            exit_code = 1
        else:
            print("    nothing flagged — 0 anomalies. This is the opening frame.")
    else:
        print(f"  WHAT THE DETECTOR WILL SAY  ({note})")
        if not anomalies:
            print("    NOTHING FLAGGED — the spike is too small for the threshold.")
            exit_code = 1
        for anomaly in anomalies:
            marker = "  <-- the incident" if anomaly.service == INCIDENT_SERVICE else ""
            print(
                f"    {anomaly.service:<14} {anomaly.date}  "
                f"${anomaly.cost:,.2f} vs baseline ${anomaly.service_mean:,.2f}  "
                f"z={anomaly.z_score}  {anomaly.severity}{marker}"
            )
        strays = [a.service for a in anomalies if a.service != INCIDENT_SERVICE]
        if strays:
            print(
                f"    WARNING: {', '.join(sorted(set(strays)))} also flagged — "
                "the estate was meant to be quiet. Try another --seed."
            )
            exit_code = 1
        incident = next(
            (a for a in anomalies if a.service == INCIDENT_SERVICE), None
        )
        if incident is not None:
            money = savings_for(incident)
            if money is not None:
                print()
                print("  WHAT THE RECOMMENDER WILL COMPUTE FROM THOSE NUMBERS")
                print(
                    f"    daily excess      ${money['daily_excess']:,.2f}"
                    f"   (= cost - baseline, from the two figures above)"
                )
                print(f"    cautious / month  ${money['cautious_monthly']:,.2f}")
                print(f"    bold / month      ${money['bold_monthly']:,.2f}")
    print()
    print("  SERVE IT — paste these, then start the app:")
    print(f"    export SENTINEL_COSTS_FILE={costs_path}")
    print("    export SENTINEL_FAKE_LLM=1 SENTINEL_DEMO_RESET=1")
    print()
    print("  Security lane (optional second beat — it has no file lane, only a")
    print("  feed URL, so the payload must be served over HTTP):")
    print(f"    (cd {security_path.parent} && "
          f"python3 -m http.server {port}) &")
    print("    export SENTINEL_ALLOW_PRIVATE_TARGETS=1 SENTINEL_FEED_TTL_SECONDS=0")
    print(
        f"    export SENTINEL_SECURITY_FEED_URL="
        f"http://127.0.0.1:{port}/{security_path.name}"
    )
    print("=" * 72)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=DEFAULT_DIR / COSTS_NAME,
        help="cost dataset to write (point SENTINEL_COSTS_FILE here)",
    )
    parser.add_argument(
        "--security-out",
        type=Path,
        default=None,
        help="security dataset to write (default: alongside --out)",
    )
    parser.add_argument(
        "--spike",
        type=float,
        default=DEFAULT_SPIKE,
        help=f"the incident day's cost in USD (default {DEFAULT_SPIKE:.0f})",
    )
    parser.add_argument(
        "--security-spike",
        type=int,
        default=DEFAULT_SECURITY_SPIKE,
        help=f"public reads on the incident day (default {DEFAULT_SECURITY_SPIKE})",
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=None,
        help="newest day, ISO (default: yesterday, so the jury sees this week)",
    )
    parser.add_argument(
        "--seed", type=int, default=60, help="jitter rotation seed (default 60)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="port quoted in the printed security-feed commands (default 8765)",
    )
    parser.add_argument(
        "--healthy",
        action="store_true",
        help=(
            "write the same estate with NO incident — the demo's opening "
            "frame, so the signal cannot be pre-baked"
        ),
    )
    parser.add_argument(
        "--quiet", action="store_true", help="write the files, print only their paths"
    )
    args = parser.parse_args(argv)

    if args.spike <= 0:
        parser.error("--spike must be positive")
    end = args.end_date or (date.today() - timedelta(days=1))
    security_out = args.security_out or args.out.parent / SECURITY_NAME
    spike = None if args.healthy else args.spike
    security_spike = None if args.healthy else args.security_spike

    costs = build_costs(end, spike, args.seed)
    security = build_security(end, security_spike, args.seed)
    costs_digest = _write(args.out, costs)
    security_digest = _write(security_out, security)

    if args.quiet:
        print(args.out)
        print(security_out)
        return 0
    return report(
        costs,
        security,
        args.out.resolve(),
        security_out.resolve(),
        costs_digest,
        security_digest,
        args.port,
        args.healthy,
    )


if __name__ == "__main__":
    raise SystemExit(main())
