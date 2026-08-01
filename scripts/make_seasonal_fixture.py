"""Generate app/data/seasonal_costs.json — the estate that has a working week.

The bundled cost fixture is fourteen days long. The detector's day-of-week
baseline needs a weekday bucket large enough to be a baseline of its own
(``MIN_WEEKDAY_SAMPLES`` records AND ``n - 1 > threshold**2``, so six-plus
Saturdays at the default 2.0 threshold), which fourteen days cannot supply
at any window size. The seasonal path therefore ships untested: real code,
never exercised.

This writes ten weeks of it. Two services carry a loud weekly rhythm in
opposite directions and one is deliberately flat, and the point of the set is
the planted Saturday on ``analytics-batch``:

    a value that is unremarkable for a Tuesday and impossible for a Saturday.

A flat baseline pools weekdays and weekends into one bimodal group whose
spread is enormous, so the planted day vanishes inside it. The weekday
baseline compares Saturdays with Saturdays and it stands out immediately.
That contrast — same numbers, same detector, seasonality off then on — is
what the fixture exists to prove.

Deterministic by construction: a fixed jitter palette, no PRNG, no clock, no
network. The JSON is the artifact; this script is how it can be rebuilt and
argued with.

    python3 scripts/make_seasonal_fixture.py [--check]

``--check`` regenerates in memory and fails if the committed file has
drifted, so the fixture cannot rot silently.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "app" / "data" / "seasonal_costs.json"

WEEKS = 10
# A Sunday, so the ten weeks are whole calendar weeks. Whole weeks matter:
# the demo's date rebase shifts every dataset by whole weeks precisely so
# weekday alignment — and with it this fixture — survives the shift.
END = date(2026, 7, 5)

SATURDAY = 5
SUNDAY = 6

# Day-to-day jitter, as a balanced palette rather than random draws.
#
# The first attempt used a seeded Gaussian and the verification below caught
# it: normal noise produces 2-sigma outliers by definition, so a ten-week set
# reliably grew three or four accidental anomalies per service and the planted
# one stopped being the story. This palette is symmetric, sums to zero, and
# its largest member sits at |z| ~ 1.5 of its own spread — comfortably under
# the 2.0 threshold, by construction rather than by luck.
#
# Ten entries against a seven-day week is the trick that makes it hold per
# weekday too: 7 and 10 are coprime, so over ten weeks every weekday bucket
# receives each palette value exactly once. Every bucket therefore has the
# same spread, and no bucket can produce a flag on jitter alone.
JITTER = (-1.0, 0.6, -0.4, 1.0, -0.8, 0.2, 0.8, -0.6, 0.4, -0.2)

# service -> (weekday level, weekend level, jitter amplitude, palette rotation)
PROFILES = {
    # A nightly batch estate: five days of heavy compute, quiet weekends.
    "analytics-batch": (200.0, 40.0, 3.0, 0),
    # Retail traffic runs the other way — the weekend is the busy period.
    "checkout-api": (60.0, 110.0, 2.0, 3),
    # The control. Archive storage does not care what day it is, and the
    # seasonal path must not invent a rhythm for it.
    "storage-archive": (75.0, 75.0, 2.5, 7),
}

# (service, date, cost) — the whole reason the file exists.
#
# 199.4 is an unremarkable weekday for this service (the working week runs
# 197-203) and an impossible Saturday (the weekend runs 37-43). The incident
# it describes is a real one: a batch job that should have slept through the
# weekend ran a full weekday-sized pass instead.
#
# The value has to sit INSIDE the weekday range or the fixture proves nothing
# about seasonality, only about size — a test below asserts exactly that.
PLANTED = [("analytics-batch", date(2026, 6, 20), 199.4)]


def build() -> dict:
    start = END - timedelta(days=WEEKS * 7 - 1)
    planted = {(service, day): cost for service, day, cost in PLANTED}

    rows = []
    for service, (weekday_level, weekend_level, amplitude, rotation) in PROFILES.items():
        for offset in range(WEEKS * 7):
            day = start + timedelta(days=offset)
            level = (
                weekend_level if day.weekday() in (SATURDAY, SUNDAY) else weekday_level
            )
            jitter = JITTER[(offset + rotation) % len(JITTER)] * amplitude
            rows.append(
                {
                    "date": day.isoformat(),
                    "service": service,
                    "cost": planted.get((service, day), round(level + jitter, 2)),
                }
            )

    rows.sort(key=lambda row: (row["date"], row["service"]))
    return {
        "description": (
            "Ten weeks of synthetic daily cloud cost with a deliberate weekly "
            "rhythm: analytics-batch runs heavy on weekdays and idles at the "
            "weekend, checkout-api peaks at the weekend, storage-archive is "
            "flat. The planted 2026-06-20 Saturday on analytics-batch costs a "
            "full weekday's worth: ordinary for a Tuesday, impossible for a "
            "Saturday. A flat baseline misses it, a day-of-week baseline "
            "catches it. Generated "
            "deterministically by scripts/make_seasonal_fixture.py."
        ),
        "currency": "USD",
        "period": {"start": start.isoformat(), "end": END.isoformat()},
        "daily_costs": rows,
    }


def verify(dataset: dict) -> list[str]:
    """Measure the fixture with the product's own detector, not by eye.

    Calibration guessed is calibration wrong; this asserts the contrast the
    file is for, and that nothing else in ten weeks flags by accident.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.detection import run_detection  # noqa: PLC0415 — script-local

    records = dataset["daily_costs"]
    window = WEEKS * 7
    problems = []

    flat = run_detection(records, threshold=2.0, window=window, seasonal=False)
    seasonal = run_detection(records, threshold=2.0, window=window, seasonal=True)

    flat_hits = {(a.service, a.date) for a in flat.anomalies}
    seasonal_hits = {(a.service, a.date) for a in seasonal.anomalies}
    expected = {(service, day.isoformat()) for service, day, _ in PLANTED}

    if flat_hits:
        problems.append(f"flat baseline flagged {sorted(flat_hits)} — expected none")
    if seasonal_hits != expected:
        problems.append(
            f"weekday baseline flagged {sorted(seasonal_hits)}, expected {sorted(expected)}"
        )
    if not any(a.detector.endswith("+weekday") for a in seasonal.anomalies):
        problems.append("the weekday baseline never engaged — the fixture is too short")
    if seasonal.insufficient_data_services:
        problems.append(
            f"services short of history: {seasonal.insufficient_data_services}"
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed fixture differs from a fresh generation",
    )
    args = parser.parse_args()

    dataset = build()
    rendered = json.dumps(dataset, indent=2) + "\n"

    problems = verify(dataset)
    for problem in problems:
        print(f"FAIL  {problem}")
    if problems:
        return 1

    if args.check:
        if not OUTPUT.exists():
            print(f"FAIL  {OUTPUT} is missing")
            return 1
        if OUTPUT.read_text(encoding="utf-8") != rendered:
            print(f"FAIL  {OUTPUT} has drifted from the generator")
            return 1
        print(f"PASS  {OUTPUT.name} matches the generator, and the contrast holds")
        return 0

    OUTPUT.write_text(rendered, encoding="utf-8")
    print(
        f"wrote {OUTPUT} — {len(dataset['daily_costs'])} rows, "
        f"{WEEKS} weeks, {len(PROFILES)} services"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
