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
        "/analytics/ai",
        "/analytics/costs/forecast",
        "/analytics/costs/trend",
        "/analytics/decisions",
        "/analytics/roi",
        "/analytics/whatif",
        "/anomalies/{event_id}/analyze",
        "/anomalies/{event_id}/recommend",
        "/costs/daily",
        "/costs/summary",
        "/costs/summary/export",
        "/decisions/export",
        "/decisions/similar",
        "/fraud/signals",
        "/health",
        "/metrics/detection",
        "/ops/demo-reset",
        "/pulse",
        "/pulse/last",
        "/reflex/suggestions",
        "/security/signals"
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


def test_dashboard_ships_the_vitrin_and_pulse_controls():
    """Favicon, OG metadata and the one-click pulse chain are product."""
    page = client.get("/").text
    assert 'rel="icon"' in page
    assert 'property="og:title"' in page
    assert 'id="pulse-run"' in page
    assert client.get("/static/img/favicon.svg").status_code == 200
    app_js = client.get("/static/app.js").text
    assert "/analytics/ai" in app_js
    assert "/analytics/costs/forecast" in app_js


def test_dashboard_ships_the_unified_watch_strip():
    """Section I carries the security & fraud watch fed by the new lanes."""
    page = client.get("/").text
    assert 'id="watch-block"' in page
    assert "Unified watch" in page
    app_js = client.get("/static/app.js").text
    assert "/security/signals" in app_js
    assert "/fraud/signals" in app_js


def test_dashboard_ships_the_orchestration_transparency():
    """The trace fold, the memory fold and the chronicler briefing are
    product: the chain's actual execution is visible, not claimed."""
    app_js = client.get("/static/app.js").text
    assert "agent chain" in app_js
    assert "decision memory" in app_js
    assert "Chronicler briefing" in app_js


def test_dashboard_ships_the_value_and_demo_controls():
    """Fourth summary card, operator identity, decision rationale, the
    what-if line and the persisted pulse note are product."""
    page = client.get("/").text
    assert 'id="sum-value"' in page
    assert 'id="operator-name"' in page
    assert 'id="pulse-note"' in page
    app_js = client.get("/static/app.js").text
    assert "/pulse/last" in app_js
    assert "/analytics/whatif" in app_js
    assert "data-run-pulse" in app_js
    assert "rationale" in app_js
    assert "read-only" in app_js


def test_dashboard_ships_the_intelligence_panel():
    """Section VI typesets the /analytics aggregates — no generated numbers."""
    page = client.get("/").text
    assert 'id="sec-intelligence"' in page
    assert "operations intelligence" in page
    app_js = client.get("/static/app.js").text
    assert "/analytics/decisions" in app_js
    assert "/analytics/costs/trend" in app_js


def test_docs_are_self_hosted_under_the_strict_csp():
    """Swagger UI is vendored (static/vendor/), boots from an external script
    file, and therefore runs under the same script-src 'self' policy as the
    dashboard — no CDN exception anywhere, on any path."""
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "/static/vendor/swagger-ui-bundle.js" in docs.text
    assert "/static/vendor/swagger-init.js" in docs.text
    assert "<script>" not in docs.text  # boot must not be inline

    for path in ("/docs", "/", "/health"):
        csp = client.get(path).headers["content-security-policy"]
        assert "script-src 'self';" in csp
        assert "cdn.jsdelivr.net" not in csp

    assert client.get("/static/vendor/swagger-ui-bundle.js").status_code == 200
    assert client.get("/static/vendor/swagger-ui.css").status_code == 200
    # ReDoc is dropped rather than vendored: one API browser is product.
    assert client.get("/redoc").status_code == 404
