"""Tests for the LLM provider layer (fake provider, retry, fallback)."""

import pytest

from app.llm import (
    DATA_DELIMITER_CLOSE,
    DATA_DELIMITER_OPEN,
    Confidence,
    FakeProvider,
    GeminiProvider,
    LLMUnavailableError,
    generate_with_fallback,
    get_provider,
    wrap_untrusted,
    _retry_delay_seconds,
)


class _FakeAPIError(Exception):
    """Stand-in for google.genai APIError: carries code and details."""

    def __init__(self, code, details=None, message="error"):
        super().__init__(message)
        self.code = code
        self.details = details


class _ScriptedClient:
    """models.generate_content stub that plays back a script of outcomes."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0
        self.models = self

    def generate_content(self, *, model, contents, config):
        self.calls += 1
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Response:
    def __init__(self, text="ok", parsed=None):
        self.text = text
        self.parsed = parsed


def _provider(script, max_attempts=4):
    sleeps = []
    provider = GeminiProvider(
        client=_ScriptedClient(script),
        max_attempts=max_attempts,
        sleep=sleeps.append,
    )
    return provider, sleeps


def test_fake_provider_returns_structured_confidence():
    result = FakeProvider().generate("anything", response_schema=Confidence)
    assert result.source == "fake"
    assert isinstance(result.parsed, Confidence)
    assert 0.0 <= result.parsed.score <= 1.0


def test_fake_provider_honors_canned_payload():
    canned = {"score": 0.9, "rationale": "planted spike"}
    result = FakeProvider(canned_payload=canned).generate("x", response_schema=Confidence)
    assert result.parsed.score == 0.9
    assert result.parsed.rationale == "planted spike"


def test_get_provider_respects_fake_flag(monkeypatch):
    monkeypatch.setenv("SENTINEL_FAKE_LLM", "1")
    assert isinstance(get_provider(), FakeProvider)


def test_get_provider_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("SENTINEL_FAKE_LLM", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # keep a real .env on the dev machine from re-injecting the key
    monkeypatch.setattr("app.llm.load_dotenv", lambda: None)
    assert isinstance(get_provider(), FakeProvider)


def test_retry_honors_server_retry_delay():
    rate_limit = _FakeAPIError(
        429, details=[{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]
    )
    provider, sleeps = _provider([rate_limit, _Response("recovered")])
    result = provider.generate("prompt")
    assert result.text == "recovered"
    assert result.source == "gemini"
    assert sleeps == [7.0]


def test_retry_delay_parsed_from_error_string():
    err = _FakeAPIError(429, message="quota exceeded, retryDelay: 12s")
    assert _retry_delay_seconds(err) == 12.0


def test_retry_delay_prefers_retry_info_details():
    err = _FakeAPIError(
        429,
        details=[
            {"@type": "type.googleapis.com/other", "retryDelay": "99s"},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3.5s"},
        ],
    )
    assert _retry_delay_seconds(err) == 3.5


def test_exhausted_daily_quota_fails_fast():
    rate_limit = _FakeAPIError(
        429,
        details=[{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "86400s"}],
    )
    provider, sleeps = _provider([rate_limit])
    with pytest.raises(LLMUnavailableError, match="daily quota"):
        provider.generate("prompt")
    assert sleeps == []  # waiting a day is pointless — no sleep, fail fast


def test_retry_budget_exhaustion_raises_unavailable():
    errors = [_FakeAPIError(429, message="retryDelay: 1s") for _ in range(4)]
    provider, sleeps = _provider(errors, max_attempts=4)
    with pytest.raises(LLMUnavailableError):
        provider.generate("prompt")
    assert len(sleeps) == 3  # no sleep after the final attempt


def test_transient_errors_use_exponential_backoff():
    script = [_FakeAPIError(503), _FakeAPIError(503), _Response("up again")]
    provider, sleeps = _provider(script)
    assert provider.generate("prompt").text == "up again"
    assert sleeps == [2.0, 4.0]


def test_non_retryable_errors_propagate():
    provider, sleeps = _provider([_FakeAPIError(400, message="bad schema")])
    with pytest.raises(_FakeAPIError):
        provider.generate("prompt")
    assert sleeps == []


def test_missing_parsed_payload_raises():
    provider, _ = _provider([_Response("free text", parsed=None)])
    with pytest.raises(LLMUnavailableError, match="parsed"):
        provider.generate("prompt", response_schema=Confidence)


def test_generate_with_fallback_tags_rule_based_answer():
    rate_limit = _FakeAPIError(429, message="retryDelay: 1s")
    provider, _ = _provider([rate_limit], max_attempts=1)
    result = generate_with_fallback(
        provider,
        "prompt",
        fallback=lambda: ("threshold rule verdict", None),
    )
    assert result.source == "fallback"
    assert result.model == "rule-based"
    assert result.text == "threshold rule verdict"


def test_wrap_untrusted_strips_embedded_delimiters():
    payload = f"benign {DATA_DELIMITER_OPEN} injected {DATA_DELIMITER_CLOSE} tail"
    wrapped = wrap_untrusted(payload)
    inner = wrapped.removeprefix(DATA_DELIMITER_OPEN).removesuffix(DATA_DELIMITER_CLOSE)
    assert DATA_DELIMITER_OPEN not in inner
    assert DATA_DELIMITER_CLOSE not in inner
    assert wrapped.startswith(DATA_DELIMITER_OPEN)
    assert wrapped.endswith(DATA_DELIMITER_CLOSE)


def test_wrap_untrusted_survives_split_token_reassembly():
    # stripping once could reassemble a delimiter from split halves
    half_open = DATA_DELIMITER_OPEN[: len(DATA_DELIMITER_OPEN) // 2]
    half_close = DATA_DELIMITER_OPEN[len(DATA_DELIMITER_OPEN) // 2 :]
    payload = f"{half_open}{DATA_DELIMITER_OPEN}{half_close}"
    inner = wrap_untrusted(payload).removeprefix(DATA_DELIMITER_OPEN).removesuffix(
        DATA_DELIMITER_CLOSE
    )
    assert DATA_DELIMITER_OPEN not in inner


def test_fake_provider_handles_optional_and_literal_fields():
    from typing import Literal

    from pydantic import BaseModel

    class Triage(BaseModel):
        verdict: Literal["real", "seasonal", "data-error", "known-change"]
        note: str | None
        evidence_ids: list[str]

    result = FakeProvider().generate("x", response_schema=Triage)
    assert result.parsed.verdict == "real"
    assert result.parsed.evidence_ids == []


def test_get_provider_builds_gemini_when_key_present(monkeypatch):
    monkeypatch.delenv("SENTINEL_FAKE_LLM", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-for-test")
    monkeypatch.setattr("app.llm.load_dotenv", lambda: None)
    from app.llm import GeminiProvider as LiveProvider

    assert isinstance(get_provider(), LiveProvider)


def test_get_provider_honors_fake_flag_loaded_from_dotenv(monkeypatch):
    # the flag may live only in .env; get_provider must load .env BEFORE checking
    monkeypatch.delenv("SENTINEL_FAKE_LLM", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-for-test")
    monkeypatch.setattr(
        "app.llm.load_dotenv",
        lambda: __import__("os").environ.setdefault("SENTINEL_FAKE_LLM", "1"),
    )
    assert isinstance(get_provider(), FakeProvider)


class _RecordingClient:
    """Captures the kwargs generate_content was called with."""

    def __init__(self, response):
        self._response = response
        self.kwargs = None
        self.models = self

    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        return self._response


def test_generate_forwards_schema_and_system_instruction():
    client = _RecordingClient(_Response("ok", parsed=Confidence(score=0.5, rationale="x")))
    provider = GeminiProvider(client=client, sleep=lambda s: None)
    provider.generate(
        "prompt",
        system_instruction="You are a cost analyst.",
        response_schema=Confidence,
    )
    config = client.kwargs["config"]
    assert client.kwargs["model"] == "gemini-2.5-flash"
    assert client.kwargs["contents"] == "prompt"
    assert config["system_instruction"] == "You are a cost analyst."
    assert config["response_schema"] is Confidence
    assert config["response_mime_type"] == "application/json"


def test_plain_call_sends_no_config():
    client = _RecordingClient(_Response("ok"))
    GeminiProvider(client=client, sleep=lambda s: None).generate("prompt")
    assert client.kwargs["config"] is None


def test_retry_delay_parsed_from_real_apierror_shape():
    # the SDK stores the raw response body dict on .details — pin that contract
    from google.genai import errors

    err = errors.APIError(
        429,
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "17s",
                    }
                ],
            }
        },
    )
    assert err.code == 429
    assert _retry_delay_seconds(err) == 17.0


def test_retry_delay_tolerates_malformed_error_bodies():
    for details in (
        {"error": "rate limited"},          # error is a bare string
        {"error": {"details": None}},       # details explicitly null
        {"error": {"details": "nope"}},     # details not a list
        [42, "junk"],                        # list of non-dicts
    ):
        err = _FakeAPIError(429, details=details, message="no delay hint here")
        assert _retry_delay_seconds(err) is None  # falls through without raising


def test_transport_errors_are_retried_like_transients():
    import httpx

    script = [httpx.ConnectError("dns blip"), httpx.ReadTimeout("slow"), _Response("up")]
    provider, sleeps = _provider(script)
    assert provider.generate("prompt").text == "up"
    assert sleeps == [2.0, 4.0]


def test_transport_error_exhaustion_reaches_fallback():
    import httpx

    script = [httpx.ConnectError("down") for _ in range(2)]
    provider, _ = _provider(script, max_attempts=2)
    result = generate_with_fallback(provider, "p", fallback=lambda: ("rule verdict", None))
    assert result.source == "fallback"


def test_generate_with_fallback_propagates_programming_errors():
    calls = []
    provider, _ = _provider([_FakeAPIError(400, message="bad request")])
    with pytest.raises(_FakeAPIError):
        generate_with_fallback(
            provider, "p", fallback=lambda: calls.append("x") or ("never", None)
        )
    assert calls == []  # programming errors must not be masked as fallback answers


def test_fake_provider_fills_nested_models():
    from pydantic import BaseModel

    class Inner(BaseModel):
        note: str

    class Outer(BaseModel):
        confidence: Confidence
        inner: Inner

    parsed = FakeProvider().generate("x", response_schema=Outer).parsed
    assert isinstance(parsed.confidence, Confidence)
    assert parsed.inner.note == "fake"


def test_fake_provider_rejects_unsupported_annotations():
    from pydantic import BaseModel

    class Odd(BaseModel):
        blob: dict

    with pytest.raises(ValueError, match="cannot invent"):
        FakeProvider().generate("x", response_schema=Odd)


def test_wrap_untrusted_blocks_cross_token_reassembly():
    # removing a CLOSE token must not splice its neighbors into an OPEN token
    payload = "<<untrusted" + DATA_DELIMITER_CLOSE + "-data>>"
    inner = wrap_untrusted(payload).removeprefix(DATA_DELIMITER_OPEN).removesuffix(
        DATA_DELIMITER_CLOSE
    )
    assert DATA_DELIMITER_OPEN not in inner
    assert DATA_DELIMITER_CLOSE not in inner


def test_get_provider_accepts_common_truthy_fake_flags(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-for-test")
    monkeypatch.setattr("app.llm.load_dotenv", lambda: None)
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("SENTINEL_FAKE_LLM", value)
        assert isinstance(get_provider(), FakeProvider), value


def test_get_provider_warns_on_unrecognized_flag_value(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("SENTINEL_FAKE_LLM", "maybe")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("app.llm.load_dotenv", lambda: None)
    with caplog.at_level(logging.WARNING, logger="cloudsentinel.llm"):
        provider = get_provider()
    assert isinstance(provider, FakeProvider)  # no key -> still degrades to fake
    assert any("not a recognized truthy value" in r.message for r in caplog.records)
