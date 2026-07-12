"""Tests for the dashboard route and static assets."""

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_dashboard_served_at_root():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "CloudSentinel" in response.text


def test_static_assets_served():
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_dashboard_hidden_from_openapi_schema():
    paths = client.get("/openapi.json").json()["paths"]
    assert "/" not in paths
    assert set(paths) == {
        "/actions",
        "/actions/{action_id}/approve",
        "/actions/{action_id}/reject",
        "/anomalies",
        "/costs/daily",
        "/costs/summary",
        "/costs/summary/export",
        "/health"
    }


def test_security_headers_present_on_dashboard():
    response = client.get("/")
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_security_headers_present_on_api():
    response = client.get("/health")
    assert response.status_code == 200
    assert "content-security-policy" in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"
