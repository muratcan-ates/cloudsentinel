"""Run the golden-set agent-chain eval and print the scorecard.

Usage:
    .venv/bin/python scripts/eval_harness.py [--cases 200] [--threshold 2.0] [--json]

Runs entirely on the deterministic provider against a throwaway
database — no key, no quota, no writes to the dev DB. The measured
numbers land in docs/EVAL_SCORECARD.md; tests/test_llm_eval.py asserts
the same invariants on a compact sweep in CI.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The environment must be pinned BEFORE app modules import: fake provider
# (never a live call) and an isolated database (never the dev DB).
os.environ["SENTINEL_FAKE_LLM"] = "1"
_db_handle, _db_path = tempfile.mkstemp(prefix="evalset-", suffix=".db")
os.close(_db_handle)
os.environ["SENTINEL_DB_PATH"] = _db_path

from app.evalset import evaluate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=int, default=200)
    parser.add_argument("--threshold", type=float, default=2.0)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    report = evaluate(count=args.cases, threshold=args.threshold)
    report.pop("results")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"CloudSentinel agent-chain eval — {report['cases']} cases, "
              f"threshold {report['threshold']}, provider {report['provider']}")
        print()
        rows = [
            ("signals detected", report["signals"]),
            ("proposals filed", report["proposals"]),
            ("flagged narrative figures", report["flagged_narratives"]),
            ("savings-formula mismatches", report["money_mismatches"]),
            ("unsafe actions", report["unsafe_actions"]),
            ("phantom proposals (no signal)", report["phantom_proposals"]),
            ("signals left without a card", report["unproposed_signals"]),
            ("quiet cases", report["quiet_cases"]),
            ("quiet detector false positives", report["quiet_false_positives"]),
            ("spike cases proposing", f"{report['spike_proposing']}/{report['spike_cases']}"),
            ("latency per case mean / p95 / max (ms)",
             f"{report['latency_ms_mean']} / {report['latency_ms_p95']} / {report['latency_ms_max']}"),
        ]
        width = max(len(label) for label, _ in rows)
        for label, value in rows:
            print(f"  {label.ljust(width)}  {value}")
        print()
        print("measured on the deterministic provider — this scores the pipeline's")
        print("contract, not live-model quality (live eval set: backlog B8)")

    Path(_db_path).unlink(missing_ok=True)
    failures = (
        report["flagged_narratives"]
        + report["money_mismatches"]
        + report["unsafe_actions"]
        + report["phantom_proposals"]
        + report["unproposed_signals"]
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
