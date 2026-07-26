"""Convert a real cloud billing CSV export into the cost-dataset JSON.

The credential-free live-data trial (backlog A4): download a billing
export from the provider console — no API keys, no SDKs — convert it
here, then serve it through the cost lane:

    .venv/bin/python scripts/import_costs.py billing.csv -o live_costs.json
    SENTINEL_COSTS_FILE=live_costs.json make run

Column names are matched case-insensitively against the headers the
big providers actually ship (Azure Cost Management exports, AWS Cost
and Usage Report) plus the generic date/service/cost triple. Rows that
do not parse are counted and dropped — the summary names them, nothing
fails silently. Duplicate (service, date) rows are summed, matching
the app's own series semantics.
"""

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

DATE_COLUMNS = (
    "date",
    "usagedate",
    "usage_date",
    "usagestartdate",
    "lineitem/usagestartdate",
)
SERVICE_COLUMNS = (
    "service",
    "servicename",
    "service_name",
    "productname",
    "product/productname",
    "metercategory",
)
COST_COLUMNS = (
    "cost",
    "costinbillingcurrency",
    "pretaxcost",
    "costusd",
    "unblendedcost",
    "lineitem/unblendedcost",
)
CURRENCY_COLUMNS = ("currency", "billingcurrency", "currencycode")


def _pick(fieldnames: list[str], candidates: tuple[str, ...]) -> str | None:
    lowered = {name.strip().lower(): name for name in fieldnames}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def _iso_day(value: str) -> str | None:
    # Exports ship dates as YYYY-MM-DD, ISO datetimes, or MM/DD/YYYY.
    raw = value.strip()
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        pass
    parts = raw.split("/")
    if len(parts) == 3:
        try:
            month, day, year = (int(p) for p in parts)
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


def convert(rows: list[dict], currency_arg: str | None = None) -> tuple[dict, int]:
    """Aggregate raw CSV rows into the dataset contract.

    Returns (dataset, dropped_row_count). Raises ValueError when the
    header carries none of the known column names or no row survives.
    """
    if not rows:
        raise ValueError("the export has no data rows")
    fieldnames = list(rows[0].keys())
    date_col = _pick(fieldnames, DATE_COLUMNS)
    service_col = _pick(fieldnames, SERVICE_COLUMNS)
    cost_col = _pick(fieldnames, COST_COLUMNS)
    if not (date_col and service_col and cost_col):
        raise ValueError(
            "unrecognized header — need date/service/cost columns "
            f"(got: {', '.join(fieldnames)})"
        )
    currency_col = _pick(fieldnames, CURRENCY_COLUMNS)

    totals: dict[tuple[str, str], float] = {}
    currencies: set[str] = set()
    dropped = 0
    for row in rows:
        day = _iso_day(str(row.get(date_col) or ""))
        service = str(row.get(service_col) or "").strip()
        try:
            cost = float(str(row.get(cost_col) or "").strip())
        except ValueError:
            cost = None
        if not (day and service) or cost is None:
            dropped += 1
            continue
        totals[(day, service)] = totals.get((day, service), 0.0) + cost
        if currency_col and str(row.get(currency_col) or "").strip():
            currencies.add(str(row[currency_col]).strip().upper())

    if not totals:
        raise ValueError("no row survived parsing — check the export format")
    records = [
        {"date": day, "service": service, "cost": round(cost, 6)}
        for (day, service), cost in sorted(totals.items())
    ]
    dates = [r["date"] for r in records]
    currency = currency_arg or (currencies.pop() if len(currencies) == 1 else "USD")
    dataset = {
        "description": "imported cloud billing export (credential-free trial)",
        "currency": currency,
        "period": {"start": min(dates), "end": max(dates)},
        "daily_costs": records,
    }
    return dataset, dropped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("export_csv", help="billing export CSV from the provider console")
    parser.add_argument(
        "-o",
        "--output",
        default="live_costs.json",
        help="dataset JSON to write (point SENTINEL_COSTS_FILE here)",
    )
    parser.add_argument(
        "--currency", default=None, help="override the currency label"
    )
    args = parser.parse_args(argv)

    with open(args.export_csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    try:
        dataset, dropped = convert(rows, args.currency)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    Path(args.output).write_text(json.dumps(dataset, indent=2) + "\n")
    records = dataset["daily_costs"]
    services = sorted({r["service"] for r in records})
    print(
        f"{len(rows)} rows read, {dropped} dropped -> {len(records)} daily records\n"
        f"period {dataset['period']['start']} → {dataset['period']['end']} "
        f"({dataset['currency']}), {len(services)} services: "
        f"{', '.join(services[:6])}{'…' if len(services) > 6 else ''}\n"
        f"wrote {args.output} — serve it with SENTINEL_COSTS_FILE={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
