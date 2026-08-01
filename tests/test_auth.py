"""/auth — local username/password identity (register, login, me, roles)."""

import time

import pytest
from fastapi.testclient import TestClient

from app import auth, db
from main import app
from tests.test_analytics import run_chain


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clean_login_throttle():
    """The failure counters are process-global; every test starts clean."""
    auth.reset_login_throttle()
    yield
    auth.reset_login_throttle()


@pytest.fixture
def ip_guard_off(monkeypatch):
    """Take main.py's per-IP login guard out of the picture.

    Two reasons, both about isolation. These tests are assertions about the
    per-username throttle, and an outer limiter answering 429 first would
    make them pass for the wrong reason. More importantly its bucket is
    keyed by client host and lives for the life of the process — every
    sign-in any test performs is spent from the same minute's budget, so a
    test that fires a dozen of them would leak a lockout into whatever runs
    next. A limit of 0 disables the guard before it records anything.
    """
    monkeypatch.setenv("SENTINEL_LOGIN_RATE_LIMIT_PER_MINUTE", "0")


def _sign_in(client, username, password="password-99"):
    return client.post(
        "/auth/login", json={"username": username, "password": password}
    )


def _wrong_password(client, username, times=1):
    """Burn `times` failed sign-ins; returns the last response."""
    for _ in range(times):
        response = _sign_in(client, username, password="wrong-pw-000")
    return response


def _token(client, username, role="approver"):
    client.post(
        "/auth/register",
        json={"username": username, "password": "password-99", "role": role},
    )
    login = client.post(
        "/auth/login", json={"username": username, "password": "password-99"}
    )
    return login.json()["token"]


def test_approve_derives_operator_identity_from_session(client):
    token = _token(client, "erin")
    body = run_chain(client, service="ec2", occurred_on="2026-07-12", verdict=None)
    action_id = body["action_id"]
    # Body claims a different actor; the server-derived identity must win.
    response = client.post(
        f"/actions/{action_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"actor": "not-erin", "rationale": "looks right"},
    )
    assert response.status_code == 200
    assert response.json()["decided_by"] == "erin"


def test_decision_without_token_keeps_body_actor(client):
    body = run_chain(client, service="rds", occurred_on="2026-07-12", verdict=None)
    action_id = body["action_id"]
    response = client.post(
        f"/actions/{action_id}/reject",
        json={"actor": "cli-bot", "rationale": "cli-driven rejection"},
    )
    assert response.status_code == 200
    assert response.json()["decided_by"] == "cli-bot"


def test_register_login_me_flow(client):
    reg = client.post(
        "/auth/register",
        json={"username": "alice", "password": "s3cret-pw!", "role": "approver"},
    )
    assert reg.status_code == 201
    # Security: self-registration can NEVER grant an elevated role — the
    # requested 'approver' is ignored and the account is created as 'viewer'.
    assert reg.json()["role"] == "viewer"
    assert "password" not in reg.json()
    assert "password_hash" not in reg.json()

    login = client.post(
        "/auth/login", json={"username": "alice", "password": "s3cret-pw!"}
    )
    assert login.status_code == 200
    token = login.json()["token"]
    assert token

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "alice"
    assert me.json()["role"] == "viewer"


def test_login_rejects_a_bad_password(client):
    client.post("/auth/register", json={"username": "bob", "password": "correct-pw1"})
    bad = client.post(
        "/auth/login", json={"username": "bob", "password": "wrong-pw-123"}
    )
    assert bad.status_code == 401


def test_me_requires_a_valid_token(client):
    assert client.get("/auth/me").status_code == 401
    forged = client.get("/auth/me", headers={"Authorization": "Bearer nope"})
    assert forged.status_code == 401


def test_duplicate_username_conflicts(client):
    client.post("/auth/register", json={"username": "carol", "password": "password-12"})
    dup = client.post(
        "/auth/register", json={"username": "carol", "password": "password-34"}
    )
    assert dup.status_code == 409


def test_invalid_role_is_rejected(client):
    response = client.post(
        "/auth/register",
        json={"username": "dave", "password": "password-12", "role": "root"},
    )
    assert response.status_code == 422


def test_self_registration_cannot_become_admin(client):
    """Security regression: an anonymous caller can never self-elevate."""
    reg = client.post(
        "/auth/register",
        json={"username": "mallory", "password": "password-99", "role": "admin"},
    )
    assert reg.status_code == 201
    assert reg.json()["role"] == "viewer"


def test_logout_revokes_the_session(client):
    """Security: a token must be revocable — logout kills it immediately."""
    token = _token(client, "nina")
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=auth).status_code == 200
    assert client.post("/auth/logout", headers=auth).status_code == 200
    assert client.get("/auth/me", headers=auth).status_code == 401


def test_expired_sessions_are_rejected(client):
    """Security: sessions age out, so a captured token is not valid forever."""
    from app import db

    token = _token(client, "oscar")
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=auth).status_code == 200
    conn = db.connect()
    try:
        with db.writing(conn):
            conn.execute(
                "UPDATE sessions SET created_at = datetime('now', '-48 hours') "
                "WHERE token = ?",
                (token,),
            )
    finally:
        conn.close()
    assert client.get("/auth/me", headers=auth).status_code == 401


def test_login_is_rate_limited(client, monkeypatch):
    """Security: online brute-force is throttled per client IP."""
    monkeypatch.setenv("SENTINEL_LOGIN_RATE_LIMIT_PER_MINUTE", "3")
    client.post("/auth/register", json={"username": "pat", "password": "password-99"})
    codes = [
        client.post(
            "/auth/login", json={"username": "pat", "password": "wrong-pw-000"}
        ).status_code
        for _ in range(5)
    ]
    assert 429 in codes


def test_failed_logins_are_throttled_per_username(client, ip_guard_off):
    """Security: one account absorbing guesses is throttled on its own name.

    The per-IP guard cannot see a stuffing run spread over many addresses;
    this one counts failures against the account being attacked.
    """
    client.post("/auth/register", json={"username": "quinn", "password": "password-99"})
    for _ in range(auth.LOGIN_FAILURE_LIMIT):
        assert _wrong_password(client, "quinn").status_code == 401
    blocked = _wrong_password(client, "quinn")
    assert blocked.status_code == 429
    # the wait is honest: a real number of seconds, inside the window, and
    # the same number the body quotes
    retry_after = int(blocked.headers["Retry-After"])
    assert 0 < retry_after <= auth.LOGIN_FAILURE_WINDOW_SECONDS
    assert f"{retry_after}s" in blocked.json()["detail"]


def test_the_throttle_never_locks_a_correct_password_out_for_good(
    client, ip_guard_off, monkeypatch
):
    """Security: throttling delays an owner, it must never dispossess one."""
    client.post("/auth/register", json={"username": "rosa", "password": "password-99"})
    start = time.monotonic()
    monkeypatch.setattr(auth, "_now", lambda: start)
    _wrong_password(client, "rosa", times=auth.LOGIN_FAILURE_LIMIT)
    # while the window is open the owner waits too — the throttle cannot
    # know whose hands are on the keyboard
    assert _sign_in(client, "rosa").status_code == 429
    monkeypatch.setattr(
        auth, "_now", lambda: start + auth.LOGIN_FAILURE_WINDOW_SECONDS + 1
    )
    reopened = _sign_in(client, "rosa")
    assert reopened.status_code == 200
    assert reopened.json()["user"]["username"] == "rosa"


def test_hammering_a_locked_account_cannot_extend_the_lockout(
    client, ip_guard_off, monkeypatch
):
    """Security: a refused attempt is not counted, so the wait only shrinks.

    Otherwise an attacker could hold an account shut indefinitely by keeping
    the guesses coming — a lockout would become a denial of service aimed at
    the owner.
    """
    client.post("/auth/register", json={"username": "sami", "password": "password-99"})
    start = time.monotonic()
    monkeypatch.setattr(auth, "_now", lambda: start)
    _wrong_password(client, "sami", times=auth.LOGIN_FAILURE_LIMIT)
    first_wait = int(_wrong_password(client, "sami").headers["Retry-After"])
    halfway = start + auth.LOGIN_FAILURE_WINDOW_SECONDS / 2
    monkeypatch.setattr(auth, "_now", lambda: halfway)
    hammered = _wrong_password(client, "sami", times=10)
    assert hammered.status_code == 429
    assert int(hammered.headers["Retry-After"]) < first_wait
    monkeypatch.setattr(
        auth, "_now", lambda: start + auth.LOGIN_FAILURE_WINDOW_SECONDS + 1
    )
    assert _sign_in(client, "sami").status_code == 200


def test_a_correct_password_clears_the_failure_record(client, ip_guard_off):
    """A few typos then success must not leave the account on a hair trigger."""
    client.post("/auth/register", json={"username": "tariq", "password": "password-99"})
    _wrong_password(client, "tariq", times=auth.LOGIN_FAILURE_LIMIT - 1)
    assert _sign_in(client, "tariq").status_code == 200
    # the record is gone, not merely one short of the limit: the same number
    # of mistakes is affordable all over again
    for _ in range(auth.LOGIN_FAILURE_LIMIT - 1):
        assert _wrong_password(client, "tariq").status_code == 401


def test_throttling_does_not_reveal_whether_an_account_exists(client, ip_guard_off):
    """Security: the 429 must not become an account-enumeration oracle."""
    client.post("/auth/register", json={"username": "ursula", "password": "password-99"})
    real = _wrong_password(client, "ursula")
    ghost = _wrong_password(client, "nobody-here")
    assert real.status_code == ghost.status_code == 401
    assert real.json()["detail"] == ghost.json()["detail"]
    # a username nobody registered is throttled exactly like a real one
    _wrong_password(client, "nobody-here", times=auth.LOGIN_FAILURE_LIMIT - 1)
    assert _wrong_password(client, "nobody-here").status_code == 429


def test_the_throttle_map_cannot_grow_without_bound(monkeypatch):
    """Security: the keys are attacker-chosen, so memory must be capped.

    Reaches into the counter map directly — the whole claim is about what
    the process is holding, which no endpoint can show.
    """
    start = time.monotonic()
    monkeypatch.setattr(auth, "_now", lambda: start)
    for index in range(50):
        auth._record_login_failure(f"sprayed-{index}")
    assert len(auth._login_failures) == 50
    # entries age out: one write after the window closes sweeps the rest
    monkeypatch.setattr(
        auth, "_now", lambda: start + auth.LOGIN_FAILURE_WINDOW_SECONDS + 1
    )
    auth._record_login_failure("the-only-live-one")
    assert set(auth._login_failures) == {"the-only-live-one"}
    # and a spray fast enough to stay inside the window still hits the cap
    monkeypatch.setattr(auth, "LOGIN_FAILURE_MAX_TRACKED", 3)
    for index in range(20):
        auth._record_login_failure(f"flood-{index}")
    assert len(auth._login_failures) <= 3


def test_invalidating_a_user_kills_every_session_they_hold(client, ip_guard_off):
    """Security: revocation must reach the tokens you are not holding."""
    registered = client.post(
        "/auth/register", json={"username": "vera", "password": "password-99"}
    )
    user_id = registered.json()["id"]
    laptop = _sign_in(client, "vera").json()["token"]
    phone = _sign_in(client, "vera").json()["token"]
    assert laptop != phone
    client.post("/auth/register", json={"username": "wendy", "password": "password-99"})
    bystander = _sign_in(client, "wendy").json()["token"]

    conn = db.connect()
    try:
        with db.writing(conn):
            revoked = auth.invalidate_user_sessions(conn, user_id)
    finally:
        conn.close()

    assert revoked == 2
    for token in (laptop, phone):
        assert (
            client.get(
                "/auth/me", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 401
        )
    # another account's session is none of its business
    assert (
        client.get(
            "/auth/me", headers={"Authorization": f"Bearer {bystander}"}
        ).status_code
        == 200
    )


def test_login_sweeps_dead_sessions_so_the_table_stays_bounded(client, ip_guard_off):
    """The only path that grows the session table is the one that prunes it."""
    registered = client.post(
        "/auth/register", json={"username": "yusuf", "password": "password-99"}
    )
    user_id = registered.json()["id"]
    live = _sign_in(client, "yusuf").json()["token"]
    conn = db.connect()
    try:
        with db.writing(conn):
            conn.execute(
                "INSERT INTO sessions (token, user_id, created_at) "
                "VALUES ('long-dead', ?, datetime('now', '-48 hours'))",
                (user_id,),
            )
    finally:
        conn.close()

    fresh = _sign_in(client, "yusuf")
    assert fresh.status_code == 200

    conn = db.connect()
    try:
        tokens = {row["token"] for row in conn.execute("SELECT token FROM sessions")}
    finally:
        conn.close()
    assert "long-dead" not in tokens
    # the sweep only takes what expiry already refused
    assert {live, fresh.json()["token"]} <= tokens


def test_bootstrap_elevation_revokes_the_tokens_it_outranks(client, monkeypatch):
    """Security: a privilege change must not be inherited by an old token.

    Nothing in a session row records the level it was issued at, so the
    only safe answer to a promotion is to cut every session and make the
    account sign in again at its new level.
    """
    monkeypatch.setenv("SENTINEL_LOGIN_RATE_LIMIT_PER_MINUTE", "0")
    client.post(
        "/auth/register", json={"username": "ops-lead", "password": "landing-strip-77"}
    )
    token = _sign_in(client, "ops-lead", password="landing-strip-77").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=headers).json()["role"] == "viewer"

    monkeypatch.setenv("SENTINEL_ADMIN_USER", "ops-lead")
    monkeypatch.setenv("SENTINEL_ADMIN_PASSWORD", "landing-strip-77")
    auth.ensure_bootstrap_admin()

    assert client.get("/auth/me", headers=headers).status_code == 401
    reissued = _sign_in(client, "ops-lead", password="landing-strip-77")
    assert reissued.json()["user"]["role"] == "admin"


def test_bootstrap_never_promotes_an_account_it_cannot_prove_is_its_own(
    client, monkeypatch
):
    """Security: on a disk that survives a restart the configured username
    may already belong to a stranger, and promoting them would hand away
    the estate. The env password is the proof of ownership."""
    monkeypatch.setenv("SENTINEL_LOGIN_RATE_LIMIT_PER_MINUTE", "0")
    client.post(
        "/auth/register", json={"username": "ops-lead", "password": "squatter-pw-11"}
    )
    token = _sign_in(client, "ops-lead", password="squatter-pw-11").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    monkeypatch.setenv("SENTINEL_ADMIN_USER", "ops-lead")
    monkeypatch.setenv("SENTINEL_ADMIN_PASSWORD", "landing-strip-77")
    auth.ensure_bootstrap_admin()

    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200  # their session is left alone
    assert me.json()["role"] == "viewer"  # and so is their role
    # nor was the operator's password planted on the account
    assert _sign_in(client, "ops-lead", password="landing-strip-77").status_code == 401


def test_readonly_mode_blocks_writes_including_delete(client, monkeypatch):
    """Security regression: read-only mode blocks every write verb, not just POST."""
    created = client.post("/routines", json={"name": "rb", "steps": ["insights"]})
    routine_id = created.json()["id"]
    monkeypatch.setenv("SENTINEL_READONLY", "1")
    blocked = client.post("/routines", json={"name": "z", "steps": ["insights"]})
    assert blocked.status_code == 403
    assert client.delete(f"/routines/{routine_id}").status_code == 403
