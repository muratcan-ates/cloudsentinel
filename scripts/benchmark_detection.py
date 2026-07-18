"""Print the detection benchmark table: detectors vs synthetic scenarios.

Usage: .venv/bin/python scripts/benchmark_detection.py [--threshold 2.0]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.benchmark import evaluate, standard_scenarios  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=2.0)
    args = parser.parse_args()

    configs = [
        ("zscore", False),
        ("mad", False),
        ("zscore", True),
    ]
    header = f"{'scenario':<24} {'detector':<16} {'tp':>3} {'fp':>3} {'fn':>3} {'precision':>10} {'recall':>7}"
    print(header)
    print("-" * len(header))
    for scenario in standard_scenarios():
        for detector, seasonal in configs:
            result = evaluate(
                scenario,
                threshold=args.threshold,
                detector=detector,
                seasonal=seasonal,
            )
            label = detector + ("+weekday" if seasonal else "")
            fmt = lambda v: "—" if v is None else f"{v:.2f}"  # noqa: E731
            print(
                f"{result.scenario:<24} {label:<16} {result.true_positives:>3} "
                f"{result.false_positives:>3} {result.false_negatives:>3} "
                f"{fmt(result.precision):>10} {fmt(result.recall):>7}"
            )


if __name__ == "__main__":
    main()
