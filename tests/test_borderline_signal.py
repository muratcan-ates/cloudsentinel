"""The borderline network blip is demo data with a purpose: the sensitivity
slider must be MEANINGFUL. It sits below the default threshold and only
surfaces when the operator lowers sensitivity."""

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_borderline_hidden_at_default_threshold():
    body = client.get("/anomalies").json()  # threshold 2.0
    assert body["anomaly_count"] == 2
    services = {a["service"] for a in body["anomalies"]}
    assert "network" not in services  # the blip stays under the bar


def test_borderline_surfaces_when_sensitivity_drops():
    loose = client.get("/anomalies?threshold=1.5").json()
    blip = next(
        (a for a in loose["anomalies"] if a["service"] == "network"), None
    )
    assert blip is not None, "lowering the threshold must reveal the borderline"
    assert blip["date"] == "2026-07-01"
    assert blip["severity"] == "warning"  # a warning, not a critical
    assert 1.5 <= abs(blip["z_score"]) < 2.0  # genuinely borderline
