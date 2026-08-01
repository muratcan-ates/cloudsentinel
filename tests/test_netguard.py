"""The outbound target guard — a configured URL may not aim back inside.

Hermetic by construction: the only test that needs name resolution
monkeypatches the resolver, so the suite never touches the network.
"""

import pytest

from app import feeds, netguard


def test_plaintext_http_is_refused():
    with pytest.raises(netguard.UnsafeTarget):
        netguard.assert_safe_url("http://feeds.example.com/costs")


def test_non_http_schemes_are_refused():
    for url in ("file:///etc/passwd", "gopher://example.com/", "ftp://example.com/x"):
        with pytest.raises(netguard.UnsafeTarget):
            netguard.assert_safe_url(url)


def test_url_without_a_host_is_refused():
    with pytest.raises(netguard.UnsafeTarget):
        netguard.assert_safe_url("https:///costs")


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",  # loopback
        "169.254.169.254",  # the cloud instance-metadata address
        "10.0.0.5",  # private
        "192.168.1.20",  # private
        "172.16.4.4",  # private
        "0.0.0.0",  # unspecified
        "[::1]",  # IPv6 loopback
        "[::ffff:127.0.0.1]",  # IPv4-mapped loopback
    ],
)
def test_literal_private_addresses_are_refused(host):
    with pytest.raises(netguard.UnsafeTarget):
        netguard.assert_safe_url(f"https://{host}/anything")


def test_a_name_that_resolves_inward_is_refused(monkeypatch):
    monkeypatch.setattr(netguard, "_resolve", lambda host: ["169.254.169.254"])
    with pytest.raises(netguard.UnsafeTarget):
        netguard.assert_safe_url("https://metadata.example.com/latest")


def test_a_public_name_is_allowed(monkeypatch):
    monkeypatch.setattr(netguard, "_resolve", lambda host: ["93.184.216.34"])
    url = "https://feeds.example.com/costs.json"
    assert netguard.assert_safe_url(url) == url


def test_mixed_answers_are_refused_when_any_address_is_private(monkeypatch):
    monkeypatch.setattr(
        netguard, "_resolve", lambda host: ["93.184.216.34", "127.0.0.1"]
    )
    with pytest.raises(netguard.UnsafeTarget):
        netguard.assert_safe_url("https://rebind.example.com/costs")


def test_an_unresolvable_name_passes_the_guard(monkeypatch):
    """The request that follows cannot reach anything either.

    Failing here would only turn a network error into a different error —
    and would break every offline run pointed at a reserved .test domain.
    """

    def _boom(host):
        raise OSError("Name or service not known")

    monkeypatch.setattr(netguard, "_resolve", _boom)
    url = "https://example.test/costs"
    assert netguard.assert_safe_url(url) == url


def test_the_developer_escape_hatch_reopens_local_targets(monkeypatch):
    monkeypatch.setenv(netguard.ALLOW_PRIVATE_ENV, "1")
    assert netguard.assert_safe_url("http://127.0.0.1:8001/costs")


def test_a_feed_aimed_at_the_metadata_service_falls_back_to_mock(monkeypatch):
    """End to end: the guard fires before any socket opens."""
    feeds.reset_cache()
    monkeypatch.setenv(feeds.COSTS_FEED_ENV, "https://169.254.169.254/costs")
    monkeypatch.delenv(feeds.COSTS_SOURCE_ENV, raising=False)

    def _forbidden(*args, **kwargs):
        raise AssertionError("the guard must refuse before the request")

    monkeypatch.setattr(netguard, "_resolve", _forbidden)
    with pytest.raises(feeds.FeedUnavailable):
        feeds._fetch("costs", "https://169.254.169.254/costs", lambda payload: payload)
    feeds.reset_cache()
