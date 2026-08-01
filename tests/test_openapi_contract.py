"""The published API contract, asserted as a contract.

Every other suite tests what an endpoint *does*. This one tests what the
API *promises* — the OpenAPI document that /docs renders and that a jury
member, an integrator or a future teammate reads before they read any
code. That document is generated, which is exactly why it needs guarding:
nobody edits it, so nobody notices when it rots. A route added without a
docstring, a handler that quietly returns a bare ``dict`` instead of a
model, a tag typo that files an endpoint under a section that does not
exist, a response model that grows a field named ``password_hash`` — all
of these ship green through the functional suites and land in the public
schema.

Five promises are pinned here:

* **Documented.** Every operation carries a summary and a description that
  opens with real prose, so /docs is readable without the source next to it.
* **Typed.** Every JSON success response resolves to a declared model,
  never a free-form object. The handful of genuine exceptions (file
  downloads, an empty 204) are named individually below with the reason,
  so an exception stays a decision rather than a habit.
* **Filed.** Tags come from a closed vocabulary, the untagged platform
  routes are pinned, and one resource never splits across two sections.
* **Explicit about failure.** Every declared error carries a description,
  every id-addressed operation documents its 404, and the human-in-the-loop
  state machine publishes the 409 that test_actions.py exercises.
* **Stable.** A compact digest of path + method + status codes is asserted
  against an inline expected value, so any change to the surface area is
  deliberate and shows up as a reviewable diff.

tests/test_dashboard.py already pins the set of *paths* (and that the
dashboard rooms stay out of the schema); this file deliberately goes one
level deeper — methods, status codes, models, tags and field names — and
the two are expected to fail together when someone adds an endpoint.
"""

import re

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(scope="module")
def schema() -> dict:
    """The generated OpenAPI document, fetched the way a client would."""
    with TestClient(app) as client:
        return client.get("/openapi.json").json()


def operations(schema: dict):
    """Yield (path, METHOD, operation) for every documented operation."""
    for path, item in sorted(schema["paths"].items()):
        for method, operation in sorted(item.items()):
            yield path, method.upper(), operation


def referenced_schemas(node, found: set) -> set:
    """Collect every component schema name reachable from a fragment.

    A response schema is rarely a bare $ref — it can sit behind anyOf (an
    optional model), items (a list) or additionalProperties (a map), so the
    only reliable way to ask "is a declared model involved here?" is to walk
    the fragment rather than inspect its top level.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.add(ref.rsplit("/", 1)[1])
        for value in node.values():
            referenced_schemas(value, found)
    elif isinstance(node, list):
        for value in node:
            referenced_schemas(value, found)
    return found


# --------------------------------------------------------------------------
# Documented
# --------------------------------------------------------------------------

# FastAPI derives a summary from the function name for free, so a summary
# proves almost nothing on its own; the description comes from the
# docstring, which someone has to actually write.
#
# The bar is "one complete sentence", not a character count. A short
# docstring is not a bad one — "Return one routine." says everything there
# is to say about GET /routines/{id}, and a length floor would only invite
# padding. What the sentence shape does catch is the real failure: a
# handler with no docstring, a fragment like "wip", or a placeholder.
PLACEHOLDER = re.compile(r"\b(todo|fixme|tbd|xxx|wip)\b", re.IGNORECASE)


def summarised(operation: dict) -> bool:
    return bool((operation.get("summary") or "").strip())


def described(operation: dict) -> bool:
    """True when the description opens with a sentence of real prose."""
    first_line = (operation.get("description") or "").strip().split("\n")[0].strip()
    if PLACEHOLDER.search(first_line):
        return False
    return len(first_line.split()) >= 3 and first_line[-1:] in {".", "?", "!"}


def test_every_operation_carries_a_summary_and_a_description(schema):
    missing = [
        f"{method} {path}"
        for path, method, operation in operations(schema)
        if not summarised(operation) or not described(operation)
    ]
    assert missing == [], (
        "these operations reach /docs without usable prose — give the handler a "
        f"docstring opening with a plain sentence about what it does: {missing}"
    )


# --------------------------------------------------------------------------
# Typed
# --------------------------------------------------------------------------

# Success responses that do not resolve to a JSON model. Each entry states
# why, so that adding to this list is a conscious act rather than a shrug —
# three of the five below are genuine (two downloads, an empty 204) and two
# are recorded gaps. Keyed by "METHOD path status".
UNTYPED_SUCCESS_RESPONSES = {
    # File downloads: the body is a CSV attachment, declared as text/csv.
    "GET /costs/summary/export 200": "text/csv download",
    "GET /decisions/export 200": "text/csv download",
    # A 204 has no body at all, by definition.
    "DELETE /routines/{routine_id} 204": "204 carries no content",
    # Known documentation gap, recorded rather than hidden: the handler is
    # typed `-> Response` and streams a Markdown attachment, but declares no
    # response_class, so the schema advertises an empty application/json
    # body. The endpoint works; only its documentation is wrong.
    "GET /actions/{action_id}/report 200": (
        "returns a Markdown attachment but declares no response_class"
    ),
    # Second recorded gap: the handler is typed `-> dict` and returns a fixed
    # {"detail": "signed out"}. The reply is deliberately uninteresting —
    # logout answers 200 for a valid, an expired and an absent token alike —
    # but "uninteresting" is not the same as "unmodelled", and a one-field
    # model would cost nothing.
    "POST /auth/logout 200": "returns a fixed dict rather than a declared model",
}


def test_json_success_responses_resolve_to_a_declared_model(schema):
    """A 2xx JSON body must be a model, not a free-form object.

    A bare ``dict`` return type produces `{"type": "object",
    "additionalProperties": true}` — a promise of nothing. Clients cannot
    generate against it and reviewers cannot see what changed.
    """
    untyped = []
    for path, method, operation in operations(schema):
        for status, response in sorted(operation.get("responses", {}).items()):
            if not status.startswith("2"):
                continue
            key = f"{method} {path} {status}"
            if key in UNTYPED_SUCCESS_RESPONSES:
                continue
            body = (response.get("content") or {}).get("application/json")
            # No JSON content at all means a non-JSON media type, which must
            # be declared explicitly above rather than silently tolerated.
            if body is None or not referenced_schemas(body.get("schema") or {}, set()):
                untyped.append(key)
    assert untyped == [], (
        "these success responses do not resolve to a declared model — give the "
        "handler a pydantic return type, or add it to UNTYPED_SUCCESS_RESPONSES "
        f"with the reason: {untyped}"
    )


def test_untyped_exemptions_all_still_exist(schema):
    """An exemption for a route that is gone is a lie left in the file."""
    live = {
        f"{method} {path} {status}"
        for path, method, operation in operations(schema)
        for status in operation.get("responses", {})
    }
    stale = sorted(set(UNTYPED_SUCCESS_RESPONSES) - live)
    assert stale == [], f"remove these dead exemptions: {stale}"


# --------------------------------------------------------------------------
# Tagged
# --------------------------------------------------------------------------

# The closed tag vocabulary — the sections /docs groups the API into. A tag
# outside this set is almost always a typo ("analytic", "Actions"), which
# silently files an endpoint under a section of its own.
KNOWN_TAGS = {
    "actions",
    "agents",
    "analytics",
    "audit",
    "auth",
    "fraud",
    "insights",
    "market",
    "memory",
    "ops",
    "reflex",
    "routines",
    "runbooks",
    "security",
    "stream",
    "telemetry",
}

# The platform endpoints declared directly on the app rather than on a
# tagged router: the cost reads, the detection scan and the two probes.
# Pinned so that a *new* untagged endpoint fails rather than drifting into
# the schema's unnamed default section.
UNTAGGED_OPERATIONS = {
    "GET /anomalies",
    "GET /costs/daily",
    "GET /costs/summary",
    "GET /costs/summary/export",
    "GET /health",
    "GET /ready",
}


def test_tags_come_from_the_declared_vocabulary(schema):
    unknown = sorted(
        {
            tag
            for _, _, operation in operations(schema)
            for tag in operation.get("tags", [])
        }
        - KNOWN_TAGS
    )
    assert unknown == [], (
        f"unknown tags {unknown} — fix the typo, or add the tag to KNOWN_TAGS "
        "if the API really has a new section"
    )


def test_only_the_platform_endpoints_are_untagged(schema):
    untagged = {
        f"{method} {path}"
        for path, method, operation in operations(schema)
        if not operation.get("tags")
    }
    assert untagged == UNTAGGED_OPERATIONS, (
        "the untagged set changed — a new endpoint belongs on a tagged router, "
        "and only a genuine platform route should join UNTAGGED_OPERATIONS"
    )


def test_a_path_prefix_maps_to_one_set_of_tags(schema):
    """Endpoints sharing a first path segment must agree on their tags.

    Tags are allowed to span prefixes — `agents` covers /agents, /pulse and
    the /anomalies/{id}/analyze chain, because that is one story told over
    several URLs. The reverse is what goes wrong: /routines/{id}/run filed
    under a different tag than /routines splits one resource across two
    sections of /docs.
    """
    by_prefix: dict[str, dict[str, list[str]]] = {}
    for path, method, operation in operations(schema):
        tags = operation.get("tags")
        if not tags:  # the platform routes above are pinned separately
            continue
        prefix = path.split("/")[1]
        by_prefix.setdefault(prefix, {}).setdefault(",".join(tags), []).append(
            f"{method} {path}"
        )
    split = {prefix: groups for prefix, groups in by_prefix.items() if len(groups) > 1}
    assert split == {}, f"one resource, several tags: {split}"


# --------------------------------------------------------------------------
# Errors documented
# --------------------------------------------------------------------------


def test_every_declared_error_response_explains_itself(schema):
    """A status code with no description tells a client nothing."""
    bare = [
        f"{method} {path} {status}"
        for path, method, operation in operations(schema)
        for status, response in sorted(operation.get("responses", {}).items())
        if not status.startswith("2") and not (response.get("description") or "").strip()
    ]
    assert bare == [], f"these error responses need a description: {bare}"


def test_operations_taking_an_id_document_their_404(schema):
    """Anything addressed by id can be addressed by a wrong id.

    The lookup miss is the single most common thing a client has to handle,
    so it belongs in the schema rather than in a surprise at runtime.
    """
    undocumented = [
        f"{method} {path}"
        for path, method, operation in operations(schema)
        if "{" in path and "404" not in operation.get("responses", {})
    ]
    assert undocumented == [], (
        "these id-addressed operations do not document a 404: "
        f"{undocumented} — declare responses={{404: {{'description': ...}}}}"
    )


def test_the_decision_state_machine_documents_its_conflicts(schema):
    """Approve/reject/execute/reopen are the human-in-the-loop core.

    Each is a transition on a settled-or-not action, so each can be refused
    as a conflict. test_actions.py asserts the 409 behaviour; this asserts
    that the behaviour is *published*, since an integrator writing retry
    logic reads the schema, not our tests.
    """
    for verb in ("approve", "reject", "execute", "reopen"):
        responses = schema["paths"][f"/actions/{{action_id}}/{verb}"]["post"][
            "responses"
        ]
        assert "409" in responses, f"/actions/{{action_id}}/{verb} hides its conflict"


# --------------------------------------------------------------------------
# No secret-shaped field ever leaves the API
# --------------------------------------------------------------------------

SECRET_SHAPED = re.compile(
    r"secret|token|password|passwd|api[-_]?key|credential|salt|hash|private|bearer",
    re.IGNORECASE,
)

# The one secret-shaped field the API is *supposed* to emit: the session
# token, which is the entire point of a login reply. Anything else matching
# the pattern is a leak — a password hash, a salt, an internal API key —
# and must fail loudly rather than be added here.
INTENDED_SECRET_FIELDS = {"LoginResponse.token"}


def response_reachable_schemas(schema: dict) -> set:
    """Every model a client can actually receive, transitively.

    Request bodies are excluded on purpose: a login request *must* carry a
    password. The question this answers is narrower and sharper — what can
    come back out.
    """
    components = schema.get("components", {}).get("schemas", {})
    reachable: set = set()
    for _, _, operation in operations(schema):
        referenced_schemas(operation.get("responses", {}), reachable)
    frontier = set(reachable)
    while frontier:  # models nest; follow them to a fixed point
        discovered: set = set()
        for name in frontier:
            referenced_schemas(components.get(name, {}), discovered)
        frontier = discovered - reachable
        reachable |= frontier
    return reachable


def test_no_response_model_exposes_a_secret_shaped_field(schema):
    components = schema.get("components", {}).get("schemas", {})
    leaked = sorted(
        f"{name}.{field}"
        for name in response_reachable_schemas(schema)
        for field in (components.get(name, {}).get("properties") or {})
        if SECRET_SHAPED.search(field)
    )
    assert set(leaked) <= INTENDED_SECRET_FIELDS, (
        "a response model exposes a secret-shaped field: "
        f"{sorted(set(leaked) - INTENDED_SECRET_FIELDS)} — the API must not "
        "serialise credentials, hashes or salts back to a client"
    )


def test_passwords_travel_in_and_never_out(schema):
    """The credential fields exist only on request bodies.

    A password reaching a response schema means a model that reads from the
    users table got returned verbatim — the classic leak, and one that no
    functional test would notice because the value is still 'correct'.
    """
    components = schema.get("components", {}).get("schemas", {})
    password_carriers = {
        name
        for name, model in components.items()
        if any("password" in field.lower() for field in (model.get("properties") or {}))
    }
    assert password_carriers, "expected the auth models to still take a password"
    escaped = sorted(password_carriers & response_reachable_schemas(schema))
    assert escaped == [], f"password-carrying models reachable from a response: {escaped}"


# --------------------------------------------------------------------------
# Stable enough to diff
# --------------------------------------------------------------------------

# The API's surface area, one line per operation: "METHOD path status,status".
#
# HOW TO UPDATE THIS WHEN YOU ADD OR CHANGE AN ENDPOINT ON PURPOSE:
#
#   1. Make your change and be sure the endpoint is what you want.
#   2. From the repo root, print the new expected value:
#
#        SENTINEL_FAKE_LLM=1 .venv/bin/python -c "
#        from main import app
#        s = app.openapi()
#        for line in sorted(
#            f\"{m.upper()} {p} {','.join(sorted(o.get('responses', {})))}\"
#            for p, i in s['paths'].items() for m, o in i.items()
#        ):
#            print(f'    \"{line}\",')
#        "
#
#   3. Replace the whole EXPECTED_SURFACE block below with that output.
#   4. Read the diff before you commit it. That is the entire point of this
#      test: a route or a status code appearing here without anyone meaning
#      it is the failure mode being guarded against. tests/test_dashboard.py
#      pins the path set too and will need the same treatment.
EXPECTED_SURFACE = {
    "DELETE /routines/{routine_id} 204,404,422",
    "GET /actions 200,422",
    "GET /actions/{action_id}/report 200,404,422",
    "GET /agents 200",
    "GET /agents/feed 200,422",
    "GET /analytics/ai 200",
    "GET /analytics/calibration 200",
    "GET /analytics/costs/forecast 200",
    "GET /analytics/costs/trend 200,422",
    "GET /analytics/decisions 200",
    "GET /analytics/handover 200",
    "GET /analytics/headline 200",
    "GET /analytics/roi 200",
    "GET /analytics/whatif 200,422",
    "GET /anomalies 200,422",
    "GET /audit/verify 200",
    "GET /auth/me 200,401,422",
    "GET /costs/daily 200",
    "GET /costs/summary 200",
    "GET /costs/summary/export 200,422",
    "GET /decisions 200,422",
    "GET /decisions/export 200",
    "GET /decisions/similar 200,422",
    "GET /fraud/signals 200,422",
    "GET /health 200",
    "GET /insights 200",
    "GET /market/opportunities 200,422",
    "GET /metrics/backtest 200,422",
    "GET /metrics/detection 200,422",
    "GET /pulse/last 200,404",
    "GET /ready 200",
    "GET /reflex/suggestions 200,422",
    "GET /routines 200",
    "GET /routines/suggestions 200",
    "GET /routines/{routine_id} 200,404,422",
    "GET /runbooks 200",
    "GET /runbooks/match 200,422",
    "GET /security/signals 200,422",
    "GET /stream/metrics 200,404",
    "GET /telemetry/usage 200",
    "POST /actions/{action_id}/approve 200,404,409,422",
    "POST /actions/{action_id}/execute 200,404,409,422",
    "POST /actions/{action_id}/reject 200,404,409,422",
    "POST /actions/{action_id}/reopen 200,404,409,422",
    "POST /anomalies/{event_id}/analyze 200,404,409,422",
    "POST /anomalies/{event_id}/recommend 200,404,409,422",
    "POST /auth/login 200,401,422",
    "POST /auth/logout 200,422",
    "POST /auth/register 201,409,422",
    "POST /insights/self-review 200",
    "POST /ops/demo-reset 200,404,422",
    "POST /pulse 200,422",
    "POST /routines 201,422",
    "POST /routines/{routine_id}/run 200,404,422",
}


def surface_digest(schema: dict) -> set:
    """Path + method + declared status codes, one compact sorted line each."""
    return {
        f"{method} {path} {','.join(sorted(operation.get('responses', {})))}"
        for path, method, operation in operations(schema)
    }


def test_the_api_surface_is_unchanged(schema):
    """The published surface changes only when someone means it to.

    Status codes are part of the digest, not just paths: adding a 409 to a
    transition or dropping a 404 from a lookup changes the contract for
    every client even though the URL list is untouched.
    """
    actual = surface_digest(schema)
    added = sorted(actual - EXPECTED_SURFACE)
    removed = sorted(EXPECTED_SURFACE - actual)
    assert not (added or removed), (
        f"the API surface moved.\n  added:   {added}\n  removed: {removed}\n"
        "If this was deliberate, regenerate EXPECTED_SURFACE with the recipe "
        "in the comment above it, and read the diff before committing."
    )
