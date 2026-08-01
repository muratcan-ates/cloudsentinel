"""The Prometheus endpoint — format, honesty and the cost of scraping it.

Two properties matter more than the numbers. The exposition must parse
as Prometheus text (a scraper drops the whole payload on one malformed
line), and an unavailable source must be *absent* rather than zero,
because on a graph those two mean different things and only one of them
would be true.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import metrics
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def parse(body: str) -> dict[str, float]:
    """A tolerant reader of the exposition — one sample per line."""
    samples: dict[str, float] = {}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, value = line.rpartition(" ")
        samples[name.strip()] = float(value)
    return samples


def test_the_endpoint_answers_the_prometheus_content_type(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "version=0.0.4" in response.headers["content-type"]


def test_every_family_declares_help_and_type(client):
    body = client.get("/metrics").text
    families = {
        line.split(" ")[2] for line in body.splitlines() if line.startswith("# HELP ")
    }
    typed = {
        line.split(" ")[2] for line in body.splitlines() if line.startswith("# TYPE ")
    }
    assert families, "the exposition must publish at least one family"
    assert families == typed
    for line in body.splitlines():
        if line and not line.startswith("#"):
            name = line.rpartition(" ")[0].split("{")[0]
            assert name in families, f"{name} has a sample but no HELP/TYPE"


def test_the_build_info_labels_describe_this_instance(client):
    body = client.get("/metrics").text
    build = next(
        line for line in body.splitlines() if line.startswith("cloudsentinel_build_info")
    )
    assert 'version="0.3.0"' in build
    assert 'provider="fake"' in build
    assert build.endswith(" 1")


def test_the_body_ends_with_a_newline(client):
    """A scraper is entitled to a final newline; some readers drop the last
    sample without one."""
    assert client.get("/metrics").text.endswith("\n")


def test_card_states_are_counted(client):
    client.post("/pulse")
    samples = parse(client.get("/metrics").text)
    proposed = [
        value
        for name, value in samples.items()
        if name.startswith("cloudsentinel_actions{") and 'state="proposed"' in name
    ]
    assert proposed and proposed[0] >= 1


def test_an_unreadable_source_is_absent_rather_than_zero():
    """Absent and zero are different claims; only one of them is true."""
    conn = sqlite3.connect(":memory:")  # no schema at all
    body = metrics.render(
        conn,
        version="0.0.0",
        env="test",
        provider="fake",
        readonly=True,
        watch=None,
    )
    assert "cloudsentinel_build_info" in body  # always knowable
    assert "cloudsentinel_actions" not in body  # table missing → no sample
    assert "cloudsentinel_watch_degraded" not in body  # no watch given
    conn.close()


def test_label_values_are_escaped():
    conn = sqlite3.connect(":memory:")
    body = metrics.render(
        conn,
        version='0.3.0" injected="yes',
        env="test",
        provider="fake",
        readonly=False,
        watch=None,
    )
    assert 'version="0.3.0\\" injected=\\"yes"' in body
    conn.close()


def test_the_watch_condition_is_exported_when_known(client):
    body = client.get("/metrics").text
    assert "cloudsentinel_watch_configured" in body
    assert "cloudsentinel_watch_degraded" in body


def test_scraping_does_not_mutate_anything(client):
    """A read-only showcase must be able to serve a scrape."""
    before = client.get("/actions").json()
    client.get("/metrics")
    assert client.get("/actions").json() == before
