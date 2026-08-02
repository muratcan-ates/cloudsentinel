#!/usr/bin/env python3
"""Draw the Sprint 3 burndown from the dates the work actually closed.

The chart is generated rather than drawn by hand so the shape cannot drift
from the record: every step below is a story from the Sprint 3 backlog
(docs/sprint3_backlog.md, section A) dated by the commit that closed it.

    .venv/bin/python scripts/make_burndown.py

Writes ProjectManagement/Sprint3Documents/burndown_sprint3.png.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

SPRINT_START = dt.date(2026, 7, 20)
SPRINT_END = dt.date(2026, 8, 2)
COMMITTED_POINTS = 13

# (date the story closed, points burned, the commit that closed it)
BURNS = [
    (dt.date(2026, 7, 26), 3, "e5adff9 — live-data trial: credential-free billing import + source lanes"),
    (dt.date(2026, 8, 1), 3, "b14732b — deployment: the live link is real, smoke learns readonly"),
    (dt.date(2026, 8, 1), 2, "d1fce8a — market watch: standing opportunities costed against the estate"),
    (dt.date(2026, 8, 1), 2, "dbe0785 — live Gemini spike: defaults follow the -latest alias"),
    (dt.date(2026, 8, 2), 3, "c8530a9 + 8607b11 — UX pass, design language, evidence pack"),
]


def _series() -> tuple[list[dt.date], list[int]]:
    """Step series: one flat run per day, dropping as each story closes."""
    remaining = COMMITTED_POINTS
    burned_by_day: dict[dt.date, int] = {}
    for when, points, _ in BURNS:
        burned_by_day[when] = burned_by_day.get(when, 0) + points

    xs: list[dt.date] = []
    ys: list[int] = []
    day = SPRINT_START
    while day <= SPRINT_END:
        xs.append(day)
        ys.append(remaining)
        remaining -= burned_by_day.get(day, 0)
        xs.append(day)
        ys.append(remaining)
        day += dt.timedelta(days=1)
    return xs, ys


def main() -> None:
    xs, ys = _series()
    total_days = (SPRINT_END - SPRINT_START).days

    fig, ax = plt.subplots(figsize=(9, 5), dpi=170)

    ax.plot(
        [SPRINT_START, SPRINT_END],
        [COMMITTED_POINTS, 0],
        linestyle="--",
        color="#8a8a8a",
        linewidth=1.2,
        label="Ideal",
    )
    ax.plot(xs, ys, color="#2b3a8f", linewidth=2.0, label="Remaining (actual)")

    # weekend bands, the same shading the Sprint 2 chart carries
    day = SPRINT_START
    while day <= SPRINT_END:
        if day.weekday() == 5:
            ax.axvspan(day, min(day + dt.timedelta(days=2), SPRINT_END), color="#dceaf7", alpha=0.55, zorder=0)
        day += dt.timedelta(days=1)

    ax.annotate(
        "the Sprint 3 core had already landed inside\n"
        "Sprint 2, so this sprint carried proving work —\n"
        "deploy, measure, capture — which cannot be\n"
        "front-loaded the way feature work can",
        xy=(dt.date(2026, 7, 29), 10),
        xytext=(dt.date(2026, 7, 21), 6.6),
        fontsize=7.5,
        color="#333333",
        arrowprops={"arrowstyle": "-", "color": "#8a8a8a", "linewidth": 0.8},
    )

    ax.set_title(
        f"Sprint 3 Burndown — Jul 20 → Aug 2, 2026  ({COMMITTED_POINTS} story points)",
        fontsize=11,
    )
    ax.set_ylabel("Story points remaining", fontsize=9)
    ax.set_ylim(-0.4, COMMITTED_POINTS + 0.6)
    ax.set_xlim(SPRINT_START, SPRINT_END)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.grid(True, linewidth=0.4, color="#dddddd")
    ax.set_axisbelow(True)
    ax.legend(fontsize=8, frameon=False)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    out = Path(__file__).resolve().parent.parent / "ProjectManagement" / "Sprint3Documents" / "burndown_sprint3.png"
    fig.tight_layout()
    fig.savefig(out)
    print(f"wrote {out} — {total_days} days, {COMMITTED_POINTS} points, {len(BURNS)} stories")


if __name__ == "__main__":
    main()
