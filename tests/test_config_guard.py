"""The boot-time configuration audit and the public-origin profile.

Two properties that used to be constants are now configuration, and both
have a wrong default that must stay wrong-proof: a production profile
may not boot with a demo posture, and the CORS allowance may not name a
hostname we do not own.
"""

import pytest

import main
from app import configcheck


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "SENTINEL_ENV",
        configcheck.READONLY_ENV,
        configcheck.REQUIRE_APPROVER_ENV,
        configcheck.ADMIN_USER_ENV,
        configcheck.ADMIN_PASSWORD_ENV,
        configcheck.FAKE_LLM_ENV,
        configcheck.ALLOW_FAKE_ENV,
        configcheck.ALLOW_PRIVATE_TARGETS_ENV,
        "GEMINI_API_KEY",
        main.PUBLIC_ORIGIN_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def test_open_writes_are_reported(monkeypatch):
    gaps = " ".join(configcheck.findings())
    assert "writes are open to anyone" in gaps


def test_the_readonly_showcase_answers_the_write_finding(monkeypatch):
    monkeypatch.setenv(configcheck.READONLY_ENV, "1")
    assert not any("writes are open" in gap for gap in configcheck.findings())


def test_required_approvals_without_a_bootstrap_admin_are_reported(monkeypatch):
    monkeypatch.setenv(configcheck.REQUIRE_APPROVER_ENV, "1")
    assert any("no bootstrap admin" in gap for gap in configcheck.findings())


def test_a_short_admin_password_is_reported(monkeypatch):
    monkeypatch.setenv(configcheck.REQUIRE_APPROVER_ENV, "1")
    monkeypatch.setenv(configcheck.ADMIN_USER_ENV, "murat")
    monkeypatch.setenv(configcheck.ADMIN_PASSWORD_ENV, "short")
    assert any("shorter than" in gap for gap in configcheck.findings())


def test_the_fake_provider_is_reported_but_can_be_accepted(monkeypatch):
    monkeypatch.setenv(configcheck.FAKE_LLM_ENV, "1")
    assert any("fake provider" in gap for gap in configcheck.findings())
    monkeypatch.setenv(configcheck.ALLOW_FAKE_ENV, "1")
    assert not any("fake provider" in gap for gap in configcheck.findings())


def test_the_outbound_escape_hatch_is_reported(monkeypatch):
    monkeypatch.setenv(configcheck.ALLOW_PRIVATE_TARGETS_ENV, "1")
    assert any("escape hatch" in gap for gap in configcheck.findings())


def test_non_production_profiles_only_report(monkeypatch):
    """Today's render showcase must keep booting exactly as before."""
    monkeypatch.setenv("SENTINEL_ENV", "render")
    monkeypatch.setenv(configcheck.READONLY_ENV, "1")
    monkeypatch.setenv(configcheck.FAKE_LLM_ENV, "1")
    gaps = configcheck.enforce_boot()  # must not raise
    assert any("fake provider" in gap for gap in gaps)


def test_the_production_profile_refuses_a_demo_posture(monkeypatch):
    monkeypatch.setenv("SENTINEL_ENV", "production")
    monkeypatch.setenv(configcheck.FAKE_LLM_ENV, "1")
    with pytest.raises(configcheck.UnsafeConfiguration) as raised:
        configcheck.enforce_boot()
    message = str(raised.value)
    assert "writes are open to anyone" in message
    assert "fake provider" in message


def test_a_correct_production_profile_boots(monkeypatch):
    monkeypatch.setenv("SENTINEL_ENV", "production")
    monkeypatch.setenv(configcheck.REQUIRE_APPROVER_ENV, "1")
    monkeypatch.setenv(configcheck.ADMIN_USER_ENV, "murat")
    monkeypatch.setenv(configcheck.ADMIN_PASSWORD_ENV, "a-long-enough-secret")
    monkeypatch.setenv("GEMINI_API_KEY", "live-key")
    monkeypatch.setenv(configcheck.FAKE_LLM_ENV, "0")
    assert configcheck.enforce_boot() == []


def test_the_default_public_origin_is_our_deployment():
    """The squatter on the bare hostname must never be allow-listed."""
    origins = main.allowed_origins()
    assert "https://cloudsentinel-y5zh.onrender.com" in origins
    assert "https://cloudsentinel.onrender.com" not in origins


def test_the_public_origin_is_configurable(monkeypatch):
    monkeypatch.setenv(main.PUBLIC_ORIGIN_ENV, "https://a.example, https://b.example")
    origins = main.allowed_origins()
    assert origins[:2] == ["https://a.example", "https://b.example"]
    assert "http://localhost:8000" in origins
