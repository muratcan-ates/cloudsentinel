"""Prometheus text exposition — the numbers this app already knows, scrapeable.

Everything below is read from state the app keeps anyway: the decision
tables, the AI usage ledger, the self-telemetry counters and the standing
watch's own vitals. Nothing new is measured and nothing is buffered, so
turning a scrape off costs the product nothing — which is the point of
exporting rather than instrumenting.

The format is the Prometheus text exposition format, written by hand in
about forty lines rather than pulled in as a dependency. That is not
frugality for its own sake: ``prometheus_client`` would bring a process
registry, a multiprocess mode and a metrics-server thread we would then
have to reason about on a single free-tier instance, in exchange for
string formatting we can read at a glance.

What it deliberately does not do: no per-request histogram, no latency
quantiles. Those need either a registry that survives the request or a
sampling buffer, and a scrape endpoint that keeps its own state is a
memory leak waiting for a slow scraper. The pulse receipts already carry
measured durations for the runs that matter.
"""

import sqlite3

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _escape(value: str) -> str:
    """Escape a label value: backslash, quote and newline, in that order."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _line(name: str, value: float, **labels: str) -> str:
    if labels:
        rendered = ",".join(
            f'{key}="{_escape(str(val))}"' for key, val in sorted(labels.items())
        )
        return f"{name}{{{rendered}}} {value}"
    return f"{name} {value}"


def _metric(name: str, kind: str, help_text: str, samples: list[str]) -> list[str]:
    """One metric family: HELP, TYPE, then its samples (or nothing)."""
    if not samples:
        return []
    return [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}", *samples]


def _counts(conn: sqlite3.Connection, sql: str) -> list[tuple[str, int]]:
    """A grouped count, or nothing at all — a scrape may not fail the app."""
    try:
        return [(str(row[0]), int(row[1])) for row in conn.execute(sql)]
    except sqlite3.Error:
        return []


def _total(conn: sqlite3.Connection, sql: str) -> int | None:
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.Error:
        return None
    return int(row[0]) if row and row[0] is not None else 0


def render(
    conn: sqlite3.Connection,
    *,
    version: str,
    env: str,
    provider: str,
    readonly: bool,
    watch=None,
) -> str:
    """The whole exposition as one string, ending in a newline.

    ``watch`` is a WatchHealth (or None when the caller could not read
    one). A metric whose source is unavailable is omitted rather than
    reported as zero: absent and zero mean different things to whoever
    is reading the graph, and only one of them is true.
    """
    lines: list[str] = []

    lines += _metric(
        "cloudsentinel_build_info",
        "gauge",
        "Always 1; the labels carry what this instance is running.",
        [
            _line(
                "cloudsentinel_build_info",
                1,
                version=version,
                env=env,
                provider=provider,
                readonly=str(readonly).lower(),
            )
        ],
    )

    lines += _metric(
        "cloudsentinel_actions",
        "gauge",
        "Decision cards by lifecycle state.",
        [
            _line("cloudsentinel_actions", count, state=state)
            for state, count in _counts(
                conn, "SELECT state, COUNT(*) FROM actions GROUP BY state"
            )
        ],
    )

    lines += _metric(
        "cloudsentinel_decisions_total",
        "counter",
        "Operator verdicts recorded, by verdict.",
        [
            _line("cloudsentinel_decisions_total", count, verdict=verdict)
            for verdict, count in _counts(
                conn, "SELECT verdict, COUNT(*) FROM decisions GROUP BY verdict"
            )
        ],
    )

    lines += _metric(
        "cloudsentinel_ai_calls_total",
        "counter",
        "Agent model calls, by source and whether the cache answered.",
        [
            _line(
                "cloudsentinel_ai_calls_total",
                count,
                source=source.split("|")[0],
                cached=source.split("|")[1],
            )
            for source, count in _counts(
                conn,
                "SELECT source || '|' || CASE from_cache WHEN 1 THEN 'true' "
                "ELSE 'false' END, COUNT(*) FROM ai_usage GROUP BY 1",
            )
        ],
    )

    served = _total(conn, "SELECT SUM(hits) FROM telemetry_usage")
    if served is not None:
        lines += _metric(
            "cloudsentinel_requests_served_total",
            "counter",
            "Requests this instance recorded through its own telemetry.",
            [_line("cloudsentinel_requests_served_total", served)],
        )

    if watch is not None:
        lines += _metric(
            "cloudsentinel_watch_configured",
            "gauge",
            "1 when a standing watch interval is configured, 0 when scanning "
            "is request-triggered (the documented default, not a fault).",
            [_line("cloudsentinel_watch_configured", int(watch.configured))],
        )
        lines += _metric(
            "cloudsentinel_watch_degraded",
            "gauge",
            "1 when the watch has stopped watching — the 1 August freeze, "
            "where a live process kept answering 200 with a stale heartbeat.",
            [_line("cloudsentinel_watch_degraded", int(watch.degraded))],
        )
        if watch.last_pulse_age_seconds is not None:
            lines += _metric(
                "cloudsentinel_watch_last_pulse_age_seconds",
                "gauge",
                "Seconds since the newest recorded pulse; survives a restart.",
                [
                    _line(
                        "cloudsentinel_watch_last_pulse_age_seconds",
                        round(watch.last_pulse_age_seconds, 3),
                    )
                ],
            )
        lines += _metric(
            "cloudsentinel_watch_consecutive_errors",
            "gauge",
            "Failed watch ticks since the last successful beat.",
            [
                _line(
                    "cloudsentinel_watch_consecutive_errors",
                    watch.consecutive_errors,
                )
            ],
        )

    return "\n".join(lines) + "\n"
