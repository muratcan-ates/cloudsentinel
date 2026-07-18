"""Leave-one-out baseline scoring.

Under the default baseline a single extreme spike pulls up the very mean and
stdev it is then scored against, softening its own z-score — the
contaminated-baseline weakness named in the July 18 review and in
docs/sprint3_backlog.md item B6. ``leave_one_out=True`` (or
``SENTINEL_LEAVE_ONE_OUT=1``) excludes each record from its own baseline so
the signal sharpens. The knob is off by default, so every existing scan is
byte-for-byte unchanged.
"""

from fastapi.testclient import TestClient

from app.detection import run_detection
from main import app

# A quiet service — small day-to-day spread around 100 — then one hard spike.
_QUIET = [98.0, 99.0, 100.0, 101.0, 102.0, 99.0, 100.0, 101.0, 98.0]
_SPIKE_DATE = "2026-06-10"


def _records():
    days = [
        {"service": "svc", "date": f"2026-06-{i + 1:02d}", "cost": cost}
        for i, cost in enumerate(_QUIET)
    ]
    days.append({"service": "svc", "date": _SPIKE_DATE, "cost": 200.0})
    return days


def _spike(run):
    hits = [a for a in run.anomalies if a.date == _SPIKE_DATE]
    return hits[0] if hits else None


def test_leave_one_out_sharpens_a_contaminated_baseline():
    records = _records()
    contaminated = run_detection(records, 2.0, detector="zscore", leave_one_out=False)
    loo = run_detection(records, 2.0, detector="zscore", leave_one_out=True)

    spike_contaminated = _spike(contaminated)
    spike_loo = _spike(loo)
    assert spike_contaminated is not None
    assert spike_loo is not None

    # Excluding the spike from its own baseline makes its deviation strictly
    # larger — the whole point of leave-one-out scoring.
    assert abs(spike_loo.z_score) > abs(spike_contaminated.z_score)


def test_leave_one_out_is_labelled_and_recorded():
    loo = run_detection(_records(), 2.0, detector="zscore", leave_one_out=True)
    spike = _spike(loo)
    assert spike is not None
    assert spike.detector.endswith("+loo")
    assert spike.detector_params["leave_one_out"] is True


def test_default_scan_is_untouched_by_the_new_knob():
    # Regression guard: with the knob off the payload carries no leave_one_out
    # key and the detector label is unchanged, so existing scans are identical.
    default = run_detection(_records(), 2.0, detector="zscore")
    spike = _spike(default)
    assert spike is not None
    assert "+loo" not in spike.detector
    assert "leave_one_out" not in spike.detector_params


def test_anomalies_endpoint_exposes_leave_one_out():
    with TestClient(app) as client:
        default = client.get("/anomalies")
        loo = client.get("/anomalies", params={"leave_one_out": "true"})
    assert default.status_code == 200
    assert loo.status_code == 200
    default_anoms = default.json()["anomalies"]
    loo_anoms = loo.json()["anomalies"]
    # The fixture always carries a planted spike, so both scans flag something.
    assert default_anoms and loo_anoms
    assert all("+loo" not in a["detector"] for a in default_anoms)
    assert all(a["detector"].endswith("+loo") for a in loo_anoms)
    assert all(a["detector_params"].get("leave_one_out") is True for a in loo_anoms)
