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
        "/actions/{action_id}/execute",
        "/actions/{action_id}/reject",
        "/anomalies",
        "/analytics/costs/trend",
        "/analytics/decisions",
        "/anomalies/{event_id}/analyze",
        "/anomalies/{event_id}/recommend",
        "/costs/daily",
        "/costs/summary",
        "/costs/summary/export",
        "/decisions/similar",
        "/health",
        "/metrics/detection",
        "/pulse",
        "/reflex/suggestions"
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


def test_dashboard_ships_interactive_controls():
    """The palette switch and the signal sort row are part of the product."""
    page = client.get("/").text
    assert 'data-theme-choice="mission"' in page  # night mode switch
    assert 'data-anomaly-sort="z"' in page  # sortable signal table


def test_dashboard_ships_the_intelligence_panel():
    """Section VI typesets the /analytics aggregates — no generated numbers."""
    page = client.get("/").text
    assert 'id="sec-intelligence"' in page
    assert "operations intelligence" in page
    app_js = client.get("/static/app.js").text
    assert "/analytics/decisions" in app_js
    assert "/analytics/costs/trend" in app_js


def test_docs_render_under_scoped_csp():
    """Swagger UI boots from cdn.jsdelivr.net with an inline init script; the
    dashboard's strict policy would render /docs blank, so the docs pages get
    a scoped CSP while every other path keeps script-src 'self'."""
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "swagger-ui" in docs.text
    assert "https://cdn.jsdelivr.net" in docs.headers["content-security-policy"]

    for path in ("/", "/health"):
        csp = client.get(path).headers["content-security-policy"]
        assert "script-src 'self';" in csp
        assert "cdn.jsdelivr.net" not in csp
