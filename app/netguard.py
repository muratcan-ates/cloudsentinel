"""Outbound target guard — a configured URL may not be aimed back inside.

Two lanes leave this process over the network: the live data feeds
(``SENTINEL_*_FEED_URL``) and the execute webhook
(``SENTINEL_EXECUTE_WEBHOOK_URL``). Both read their target from the
environment today, so neither is anonymous user input — but the moment a
tenant configures its own integration, an unguarded fetch becomes a
server-side request forgery: the operator points the "feed" at
``http://169.254.169.254/…`` and the app dutifully reads the cloud
instance metadata for them, from inside the trust boundary.

The guard is deliberately small and runs at the network boundary itself:

* **https only** — plaintext leaks the URL (which may embed a token) to
  every hop; ``SENTINEL_ALLOW_PRIVATE_TARGETS=1`` re-opens http for a
  developer pointing at a local stub.
* **no private destinations** — literal or resolved, loopback,
  link-local (the metadata range), private, carrier-NAT, multicast,
  reserved and unspecified addresses are refused.
* **no redirects** — the callers pass ``follow_redirects=False``, because
  a public host answering ``302 → 169.254.169.254`` would otherwise walk
  straight through an address check that already passed.

A name that does not resolve is *allowed through*: the request that
follows cannot reach anything either, and failing here instead would
break every hermetic test (and every offline run) that points at a
reserved ``.test`` domain. This is a guard against being aimed inward,
not a general egress firewall — that belongs to the platform.
"""

import ipaddress
import os
import socket
from urllib.parse import urlsplit

ALLOW_PRIVATE_ENV = "SENTINEL_ALLOW_PRIVATE_TARGETS"

SAFE_SCHEMES = frozenset({"https"})
DEV_SCHEMES = frozenset({"https", "http"})


class UnsafeTarget(ValueError):
    """The configured URL points somewhere the app must not fetch."""


def allow_private_targets() -> bool:
    """Developer escape hatch: permit http and private addresses."""
    return os.environ.get(ALLOW_PRIVATE_ENV, "").strip() == "1"


def _resolve(host: str) -> list[str]:
    """Every address this hostname answers with. Isolated for tests."""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _is_forbidden(address: str) -> bool:
    """True for anything that is not a public, routable destination."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:  # not an address at all — the caller resolves instead
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def assert_safe_url(url: str) -> str:
    """Raise UnsafeTarget unless the URL is a public https destination.

    Returns the URL unchanged so a caller can wrap its argument inline.
    """
    permissive = allow_private_targets()
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UnsafeTarget("target URL cannot be parsed") from exc
    scheme = (parts.scheme or "").lower()
    allowed = DEV_SCHEMES if permissive else SAFE_SCHEMES
    if scheme not in allowed:
        raise UnsafeTarget(f"scheme {scheme or '(none)'} is not allowed")
    try:
        host = parts.hostname
    except ValueError as exc:
        raise UnsafeTarget("target URL has no usable host") from exc
    if not host:
        raise UnsafeTarget("target URL has no host")
    if permissive:
        return url
    if _is_forbidden(host):
        raise UnsafeTarget("target address is not a public destination")
    try:
        addresses = _resolve(host)
    except OSError:
        # Unresolvable: the request cannot reach anything either. Let the
        # HTTP layer fail honestly rather than mask a network error here.
        return url
    for address in addresses:
        if _is_forbidden(address):
            raise UnsafeTarget("target host resolves to a private address")
    return url
