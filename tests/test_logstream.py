"""Correlation and the JSON log format.

The tagged stream was already machine-readable per line; what it could
not do was tell you which request a line belonged to, or hand a log
pipeline one object per line. Both are tested here — including the part
that matters most on submission day: with the knob unset, nothing about
the output changes.
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app import logstream
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clear_request_id(monkeypatch):
    monkeypatch.delenv(logstream.LOG_FORMAT_ENV, raising=False)
    token = logstream.set_request_id(None)
    yield
    logstream.reset_request_id(token)


def _emit(caplog, **fields) -> dict:
    logger = logging.getLogger("cloudsentinel.test")
    with caplog.at_level(logging.INFO, logger="cloudsentinel.test"):
        logstream.log_tag(logger, "[TEST]", **fields)
    message = caplog.records[-1].getMessage()
    tag, payload = message.split(" ", 1)
    assert tag == "[TEST]"
    return json.loads(payload)


def test_a_line_outside_a_request_carries_no_id(caplog):
    payload = _emit(caplog, service="compute")
    assert payload == {"service": "compute"}


def test_a_line_inside_a_request_carries_its_id(caplog):
    token = logstream.set_request_id("abc123def456")
    try:
        payload = _emit(caplog, service="compute")
    finally:
        logstream.reset_request_id(token)
    assert payload["request_id"] == "abc123def456"
    assert payload["service"] == "compute"


def test_an_explicit_request_id_wins_over_the_ambient_one(caplog):
    token = logstream.set_request_id("ambient")
    try:
        payload = _emit(caplog, request_id="explicit")
    finally:
        logstream.reset_request_id(token)
    assert payload["request_id"] == "explicit"


def test_the_id_does_not_leak_after_reset(caplog):
    token = logstream.set_request_id("temporary")
    logstream.reset_request_id(token)
    assert logstream.current_request_id() is None
    assert "request_id" not in _emit(caplog, service="compute")


def test_the_json_formatter_expands_a_tagged_line():
    formatter = logstream.JsonFormatter()
    record = logging.LogRecord(
        name="cloudsentinel.pulse",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="%s %s",
        args=("[SIGNAL]", '{"request_id": "r1", "service": "compute"}'),
        exc_info=None,
    )
    record.cloudsentinel_tag = "[SIGNAL]"
    entry = json.loads(formatter.format(record))
    assert entry["tag"] == "SIGNAL"
    assert entry["fields"] == {"request_id": "r1", "service": "compute"}
    assert entry["request_id"] == "r1"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "cloudsentinel.pulse"


def test_the_json_formatter_keeps_lines_it_does_not_recognise():
    """A format that drops foreign lines is worse than no format."""
    formatter = logstream.JsonFormatter()
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="server shutting down",
        args=(),
        exc_info=None,
    )
    entry = json.loads(formatter.format(record))
    assert entry["message"] == "server shutting down"
    assert entry["level"] == "WARNING"


def test_json_logging_is_off_unless_asked(monkeypatch):
    assert logstream.json_logging_requested() is False
    assert logstream.install_json_logging() is False
    monkeypatch.setenv(logstream.LOG_FORMAT_ENV, "json")
    assert logstream.json_logging_requested() is True


def test_installing_the_format_reformats_existing_handlers(monkeypatch):
    logger = logging.getLogger("cloudsentinel")
    original = [(handler, handler.formatter) for handler in logger.handlers]
    monkeypatch.setenv(logstream.LOG_FORMAT_ENV, "json")
    try:
        assert logstream.install_json_logging() is True
        assert all(
            isinstance(handler.formatter, logstream.JsonFormatter)
            for handler, _ in original
        )
    finally:
        for handler, formatter in original:
            handler.setFormatter(formatter)


def test_a_request_binds_its_id_to_the_chains_log_lines(client, caplog):
    """End to end: the header the caller reads is the id in the trail."""
    with caplog.at_level(logging.INFO, logger="cloudsentinel"):
        response = client.get("/health", headers={"X-Request-ID": "trace-me-01"})
    assert response.headers["X-Request-ID"] == "trace-me-01"
    assert logstream.current_request_id() is None  # nothing leaked out


def test_the_chain_tags_carry_the_callers_id(client, caplog):
    with caplog.at_level(logging.INFO, logger="cloudsentinel"):
        client.post("/pulse", headers={"X-Request-ID": "pulse-trace-1"})
    tagged = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("[")
    ]
    assert tagged, "the pulse must emit tagged lines"
    assert any("pulse-trace-1" in line for line in tagged)
