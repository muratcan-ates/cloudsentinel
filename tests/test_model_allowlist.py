"""The model allowlist: an unvetted model never reaches the live client."""

import logging

import pytest

from app import llm


def test_the_default_and_every_panel_seat_are_allowlisted():
    """The gate must never refuse the configuration we actually ship."""
    assert llm.DEFAULT_MODEL in llm.ALLOWED_MODELS
    for seat in llm.DEFAULT_PANEL_MODELS:
        assert seat in llm.ALLOWED_MODELS


def test_no_pro_model_is_allowed():
    """The free tier grants pro models a quota of zero, so a pro seat is a
    juror that can never speak."""
    assert not [model for model in llm.ALLOWED_MODELS if "pro" in model]


def test_an_allowlisted_model_passes_the_gate():
    for model in llm.ALLOWED_MODELS:
        llm.assert_allowlisted(model)  # must not raise


def test_a_typo_is_refused_by_name():
    with pytest.raises(llm.ModelNotAllowedError) as error:
        llm.assert_allowlisted("gemini-flash-lastest")
    assert "gemini-flash-lastest" in str(error.value)
    assert "allowlist" in str(error.value)


def test_a_plausible_pro_name_is_refused():
    with pytest.raises(llm.ModelNotAllowedError):
        llm.assert_allowlisted("gemini-2.5-pro")


def test_the_gate_fires_before_a_client_is_built(monkeypatch):
    """No API key, no genai import — the refusal must come first, so an
    unvetted model never holds a connection."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(llm.ModelNotAllowedError):
        llm.GeminiProvider(model="gemini-2.5-pro")


def test_the_gate_fires_on_the_shared_client_path_too():
    """Panel seats reuse one client; the check must not be skipped there."""
    with pytest.raises(llm.ModelNotAllowedError):
        llm.GeminiProvider(model="gemini-2.5-pro", client=object())


def test_an_allowlisted_seat_builds_on_a_shared_client():
    provider = llm.GeminiProvider(model="gemini-3.5-flash", client=object())
    assert provider.model == "gemini-3.5-flash"


def test_a_refused_main_provider_degrades_to_fake_loudly(monkeypatch, caplog):
    monkeypatch.delenv("SENTINEL_FAKE_LLM", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setattr(llm, "DEFAULT_MODEL", "gemini-2.5-pro")
    with caplog.at_level(logging.ERROR, logger="cloudsentinel.llm"):
        provider = llm.get_provider()
    assert isinstance(provider, llm.FakeProvider)
    assert any("not on the allowlist" in record.message for record in caplog.records)


def test_health_never_advertises_a_backend_the_gate_would_refuse(monkeypatch):
    monkeypatch.delenv("SENTINEL_FAKE_LLM", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    assert llm.provider_mode() == "gemini"
    monkeypatch.setattr(llm, "DEFAULT_MODEL", "gemini-2.5-pro")
    assert llm.provider_mode() == "fake"


def test_one_refused_seat_sends_the_whole_panel_to_fake(monkeypatch, caplog):
    """All seats or none: a mixed panel would report a review it did not hold."""
    monkeypatch.delenv("SENTINEL_FAKE_LLM", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv(
        llm.PANEL_MODELS_ENV, "gemini-flash-latest,gemini-2.5-pro,gemini-3.5-flash"
    )
    with caplog.at_level(logging.ERROR, logger="cloudsentinel.llm"):
        providers = llm.get_panel_providers()
    assert len(providers) == 3
    assert all(isinstance(provider, llm.FakeProvider) for provider in providers)
    assert any("gemini-2.5-pro" in record.message for record in caplog.records)


def test_the_roster_still_reports_what_was_configured(monkeypatch):
    """panel_models must not filter — the caller names the rejects in the log."""
    monkeypatch.setenv(llm.PANEL_MODELS_ENV, "gemini-flash-latest,gemini-2.5-pro")
    assert llm.panel_models() == ("gemini-flash-latest", "gemini-2.5-pro")


def test_an_all_allowlisted_roster_is_not_refused(monkeypatch):
    monkeypatch.setenv("SENTINEL_FAKE_LLM", "1")
    monkeypatch.setenv(llm.PANEL_MODELS_ENV, "gemini-flash-latest,gemini-3.5-flash")
    providers = llm.get_panel_providers()
    assert len(providers) == 2


def test_the_adr_records_the_decision():
    """A refusal path nobody can explain in the review is a bug report
    waiting to happen."""
    from pathlib import Path

    adr = Path(__file__).parent.parent / "docs" / "adr" / "0003-model-allowlist.md"
    text = adr.read_text(encoding="utf-8")
    assert "ALLOWED_MODELS" in text
    assert "All seats or none" in text or "all seats or none" in text.lower()
