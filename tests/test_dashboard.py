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
    assert set(paths) == {"/anomalies", "/costs/summary"}
