"""House rules, enforced across the whole HTTP surface at once.

Every other suite in this repo tests one organ deeply. This one tests the
things that must be true of EVERY endpoint, and it discovers the endpoints
itself by walking ``app.routes`` — so a route added tomorrow is enrolled the
moment it is mounted, without anyone remembering to come back here. That is
the point: the rules below are cheap to honour and easy to forget, and the
ways they get forgotten (a new router that returns its own ``Response``, a
lookup that raises instead of 404ing, a handler that lets an exception reach
the wire) all look fine in the endpoint's own test.

The rules:

* a write verb answers 403 while ``SENTINEL_READONLY=1`` — the public demo
  link survives strangers' clicks — and a read is never blocked by it;
* an id that does not exist answers 404, never a 500;
* a malformed body answers 422 in the project's error envelope — one JSON
  object, one ``detail`` key, which is what the dashboard's fetch layer and
  every operator probing the API by hand rely on;
* every response carries X-Request-ID and the security headers, taken from
  main.py's own constants so that adding a header there tightens this test
  automatically;
* no response leaks a traceback or a filesystem path.

Where an endpoint genuinely answers something else, it is named below with
the reason and its exact expected status — never with a relaxed assertion,
and never silently, because a waiver that stops being true must fail too.
"""

import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

import main
from app import db
from main import CONTENT_SECURITY_POLICY, SECURITY_HEADERS, app

# A numeric string satisfies an int path parameter and a str one alike, so
# the same sentinel probes every "{...}" the router exposes.
UNKNOWN_ID = "987654321"

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Starlette synthesises these; they are not endpoints anyone wrote.
IMPLICIT_METHODS = frozenset({"HEAD", "OPTIONS"})


class Endpoint:
    """One (verb, path template) pair and the route object behind it."""

    def __init__(self, method: str, path: str, route: object) -> None:
        self.method = method
        self.path = path
        self.route = route

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    def __repr__(self) -> str:  # the pytest parameter id
        return f"{self.method} {self.path}"


def _discover(routes, prefix: str = "") -> list[Endpoint]:
    """Walk the router tree and return every HTTP endpoint it serves.

    FastAPI no longer flattens ``include_router`` into ``app.routes``: an
    included router sits there as one opaque object holding the real routes,
    so a flat loop over ``app.routes`` would see the six endpoints declared in
    main.py and miss the fifty behind the routers. Stepping through the
    wrapper is what makes this suite a matrix instead of a spot check.
    """
    found: list[Endpoint] = []
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            context = getattr(route, "include_context", None)
            nested = prefix + (getattr(context, "prefix", "") or "")
            found.extend(_discover(included.routes, nested))
            continue
        path = getattr(route, "path", None)
        if path is None:
            continue
        # A Mount (static files) has a path but no methods; it is not an
        # endpoint and is covered separately by its own test below.
        for method in sorted(getattr(route, "methods", None) or ()):
            if method in IMPLICIT_METHODS:
                continue
            found.append(Endpoint(method, prefix + path, route))
    return found


ENDPOINTS = _discover(app.routes)


def _required_query_names(endpoint: Endpoint) -> list[str]:
    """Query parameters the caller must supply for the route to run at all."""
    dependant = getattr(endpoint.route, "dependant", None)
    if dependant is None:  # a plain Starlette route (/openapi.json)
        return []
    return [
        field.name
        for field in dependant.query_params
        if field.field_info.is_required()
    ]


def _takes_a_body(endpoint: Endpoint) -> bool:
    return getattr(endpoint.route, "body_field", None) is not None


def _names_an_id(endpoint: Endpoint) -> bool:
    """Does this endpoint look something up by an id the caller chose?

    Path templates are the obvious case; an endpoint that demands an
    ``*_id`` query parameter (``/analytics/whatif``) is doing the same
    lookup through a different door and owes the same 404.
    """
    if "{" in endpoint.path:
        return True
    return any(name.endswith("_id") for name in _required_query_names(endpoint))


def _url(endpoint: Endpoint) -> str:
    """A concrete URL for a template, with every id set to the sentinel."""
    path = re.sub(r"\{[^}]+\}", UNKNOWN_ID, endpoint.path)
    required = _required_query_names(endpoint)
    if not required:
        return path
    query = "&".join(f"{name}={UNKNOWN_ID}" for name in required)
    return f"{path}?{query}"


WRITE_ENDPOINTS = [e for e in ENDPOINTS if e.method in WRITE_METHODS]
READ_ENDPOINTS = [e for e in ENDPOINTS if e.method == "GET"]
BODY_ENDPOINTS = [e for e in ENDPOINTS if _takes_a_body(e)]
ID_ENDPOINTS = [e for e in ENDPOINTS if _names_an_id(e)]

# The one endpoint that answers an unknown id with something other than 404,
# on purpose: POST /actions/{action_id}/reject checks its body contract
# before it touches the database, because a rejection with no rationale is
# malformed whatever action it names — decision memory and the card timeline
# are built out of those sentences, so an empty "no" is refused first. The
# waiver pins the exact status, so a future slip into 500 still fails here.
UNKNOWN_ID_EXPECTATIONS: dict[tuple[str, str], int] = {
    ("POST", "/actions/{action_id}/reject"): 422,
}

# The traceback shapes Python prints. Matching the frame line as a pattern
# (rather than searching for ".py") keeps /openapi.json clean: the API
# descriptions legitimately name modules like app/detection.py.
TRACEBACK_HEADER = "Traceback (most recent call last)"
STACK_FRAME = re.compile(r'File "[^"]+", line \d+')


@pytest.fixture
def client(monkeypatch):
    """A client for the whole matrix, with the rate limiters stood down.

    The sweeps below hit /pulse and /auth/login several times per session and
    the limiter buckets are process-global, so a 429 could otherwise show up
    as a contract violation on an endpoint that behaved perfectly. Both
    limiters have their own tests; here they would only add noise.
    """
    monkeypatch.setenv("SENTINEL_PULSE_RATE_LIMIT_PER_MINUTE", "0")
    monkeypatch.setenv("SENTINEL_LOGIN_RATE_LIMIT_PER_MINUTE", "0")
    # raise_server_exceptions=False so an endpoint that blows up is reported
    # as the failed contract it is, instead of tearing down the test with the
    # very traceback this suite exists to keep off the wire.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _call(client, endpoint: Endpoint, **kwargs):
    """Send the least surprising request this endpoint will accept."""
    if _takes_a_body(endpoint) and "json" not in kwargs and "content" not in kwargs:
        # An empty object, not a missing body: every optional-field model
        # accepts it, so the request reaches the handler rather than
        # stopping at "field required".
        kwargs["json"] = {}
    return client.request(endpoint.method, _url(endpoint), **kwargs)


def _assert_house_headers(response) -> None:
    assert response.headers.get("X-Request-ID"), "no correlation id on the response"
    assert response.headers.get("Content-Security-Policy") == CONTENT_SECURITY_POLICY
    for header, value in SECURITY_HEADERS.items():
        assert response.headers.get(header) == value, f"{header} missing or altered"


def _assert_error_envelope(response) -> None:
    """One JSON object, one `detail` key — the shape every error answers in."""
    body = response.json()
    assert isinstance(body, dict)
    assert set(body) == {"detail"}, f"error envelope grew a key: {sorted(body)}"


def test_discovery_sees_the_whole_surface():
    """A matrix that discovers nothing passes everything.

    This guards the walk itself: if a FastAPI upgrade changes how included
    routers are stored, every sweep below would silently run over an empty
    list and go green. So pin that the walk finds the routers, and that each
    probe set it feeds actually has members to probe.
    """
    paths = {endpoint.key for endpoint in ENDPOINTS}
    assert ("GET", "/health") in paths  # declared in main.py
    assert ("POST", "/pulse") in paths  # declared behind a router
    prefixes = {path.split("/")[1] for _, path in paths if path.count("/") > 1}
    assert {"actions", "analytics", "auth", "decisions", "routines"} <= prefixes
    assert len(ENDPOINTS) >= 50, "the walk lost most of the surface"
    assert WRITE_ENDPOINTS and READ_ENDPOINTS and BODY_ENDPOINTS and ID_ENDPOINTS


def test_every_waiver_still_names_a_live_route():
    """A waiver outlives the endpoint it excused unless something checks."""
    live = {endpoint.key for endpoint in ENDPOINTS}
    for key in UNKNOWN_ID_EXPECTATIONS:
        assert key in live, f"stale waiver for a route that no longer exists: {key}"


@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS, ids=repr)
def test_write_verbs_are_refused_in_readonly_mode(client, monkeypatch, endpoint):
    """The showcase promise: with SENTINEL_READONLY=1 nothing can be written.

    The refusal happens in middleware, before routing, so this holds for a
    route that has not been written yet as much as for the ones that have —
    what it really pins is that no future endpoint slips in on a verb the
    guard does not recognise.
    """
    monkeypatch.setenv("SENTINEL_READONLY", "1")
    response = _call(client, endpoint)
    assert response.status_code == 403
    _assert_error_envelope(response)
    # the short-circuit answers without ever reaching the router, which is
    # exactly the path most likely to lose the headers
    _assert_house_headers(response)


@pytest.mark.parametrize("endpoint", READ_ENDPOINTS, ids=repr)
def test_reads_keep_working_in_readonly_mode(client, monkeypatch, endpoint):
    """The other half of the promise: the panels still read while writes are shut."""
    monkeypatch.setenv("SENTINEL_READONLY", "1")
    response = _call(client, endpoint)
    assert response.status_code != 403, "read-only mode must not blind the dashboard"


@pytest.mark.parametrize("endpoint", ID_ENDPOINTS, ids=repr)
def test_an_unknown_id_answers_404(client, endpoint):
    """An id nobody ever issued is a client mistake, not a server failure."""
    response = _call(client, endpoint)
    expected = UNKNOWN_ID_EXPECTATIONS.get(endpoint.key, 404)
    assert response.status_code == expected
    _assert_error_envelope(response)
    _assert_house_headers(response)


@pytest.mark.parametrize("endpoint", BODY_ENDPOINTS, ids=repr)
def test_a_malformed_body_answers_422_in_the_house_envelope(client, endpoint):
    """A JSON array where a model is expected — the cheapest possible garbage.

    Every body on this API is an object, so a list is malformed for all of
    them without the test needing to know any endpoint's fields.
    """
    response = _call(client, endpoint, json=["not", "an", "object"])
    assert response.status_code == 422
    _assert_error_envelope(response)
    _assert_house_headers(response)
    # FastAPI's validation detail is a list of located complaints; the
    # dashboard renders `loc`/`msg`, so the shape is part of the contract
    detail = response.json()["detail"]
    assert isinstance(detail, list) and detail
    for problem in detail:
        assert {"loc", "msg", "type"} <= set(problem)


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=repr)
def test_every_response_carries_the_request_id_and_security_headers(client, endpoint):
    """Correlation and hardening are middleware-wide, and must stay that way."""
    _assert_house_headers(_call(client, endpoint))


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=repr)
def test_no_endpoint_leaks_a_traceback_or_the_database_path(client, endpoint):
    """Nothing on this API answers with the inside of the process.

    A 5xx here is a finding in itself: none of these probes asks for anything
    the server cannot answer, so a 500 means an exception escaped instead of
    being turned into an honest status.
    """
    response = _call(client, endpoint)
    assert response.status_code < 500, f"unhandled failure: {response.text[:200]}"
    body = response.text
    assert TRACEBACK_HEADER not in body
    frame = STACK_FRAME.search(body)
    assert frame is None, f"stack frame on the wire: {frame.group(0)}"
    # where the database lives is the operator's business, not the caller's
    assert str(db.db_path()) not in body


def test_the_static_mount_answers_under_the_same_rules(client):
    """The one entry in app.routes with no verbs still serves bytes to browsers.

    It is skipped by the walk above because it has no methods to probe, so
    it gets its own probe rather than a silent exemption.
    """
    response = client.get("/static/app.js")
    assert response.status_code == 200
    _assert_house_headers(response)


@pytest.mark.parametrize(
    "path", ["/", "/watch", "/broadsheet", "/static/chat.html", "/static/guide.html"]
)
def test_every_page_asks_the_browser_to_revalidate(client, path):
    """A deploy must be visible to someone who has been here before.

    Every page URL is stable across builds, so a browser left to invent its
    own freshness window will keep serving the markup it saw last time —
    along with that build's ``?v=`` asset stamps, which makes a shipped
    design change look like it never shipped. This was observed: a returning
    Safari showed a nav and a palette list from an earlier build while the
    server was serving the current one.
    """
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers.get("cache-control") == "no-cache"


def test_the_asset_stamp_is_what_lets_assets_be_cached_hard(client):
    """The other half of the bargain: pages revalidate so assets need not.

    index.html references its stylesheet and script with a version stamp, so
    a new build gives them new URLs. Drop the stamp and `no-cache` on the
    page stops being enough — the page would be fresh and its assets stale.
    """
    body = client.get("/").text
    for asset in ("/static/style.css", "/static/app.js"):
        assert f"{asset}?v=" in body, f"{asset} is referenced without a version stamp"


def test_an_unhandled_error_answers_the_flat_envelope(client, monkeypatch):
    """The last resort: the exception's own words never reach the caller.

    Note what this does NOT assert: main.py's Exception handler runs outside
    the middleware stack, so its response carries no X-Request-ID and no
    security headers — the correlation id lives in the log line instead. The
    assertion here is only that the body is the envelope and says nothing
    about what actually broke.
    """

    def explode(*args, **kwargs):
        raise RuntimeError(f"secret detail from {db.db_path()}")

    monkeypatch.setattr(main, "load_dataset", explode)
    response = client.get("/costs/summary")
    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}
    assert str(db.db_path()) not in response.text


def test_a_contended_write_answers_503_not_500(client, monkeypatch):
    """SQLite's busy timeout is a capacity signal, and it stays in the envelope.

    Unlike the last-resort handler above, this one is registered for a
    specific exception type and so runs inside the middleware stack — which
    is why a caller retrying on 503 still gets a correlation id to quote.
    """

    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "connect", busy)
    response = client.get("/decisions")
    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "2"
    _assert_error_envelope(response)
    _assert_house_headers(response)
    assert TRACEBACK_HEADER not in response.text
