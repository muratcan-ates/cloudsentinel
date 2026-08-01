"""Authentication and identity — the accountable-human foundation.

Local username/password auth with salted PBKDF2 hashing (stdlib only, no new
dependency), opaque session tokens, and four roles
(viewer < analyst < approver < admin). This is the identity layer the audit
trail needs: once a decision endpoint derives its actor from the session,
"accountable human" stops being free browser text. Bootcamp-safe — demo
accounts, synthetic data, no real PII, role selectable at registration for
the demo (a real deployment would gate elevation behind an admin).

The `current_user` / `require_role` dependencies are the seams a decision
endpoint plugs into next; nothing here changes an existing endpoint, so the
HITL flow is untouched until identity is deliberately wired in.

Hardening lives here too, because an identity is only worth what an attacker
cannot do with it: failed sign-ins are throttled per username, every session
an account holds can be revoked in one call, and dead sessions are swept on
each login so the table cannot outgrow its own 12-hour window. Stdlib only —
no rate-limiter and no session store were added for any of it.
"""

import hashlib
import logging
import math
import os
import secrets
import sqlite3
import time
from collections import deque

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db

logger = logging.getLogger("cloudsentinel.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

ROLES = ("viewer", "analyst", "approver", "admin")

# Live-ops mode: the three decision verbs (approve / reject / execute) demand
# an authenticated approver-or-admin session. Off by default, so the open
# showcase behavior stays byte-identical. The bootstrap pair seeds one
# deciding account on an ephemeral-disk deploy at lifespan startup.
REQUIRE_APPROVER_ENV = "SENTINEL_REQUIRE_APPROVER"
ADMIN_USER_ENV = "SENTINEL_ADMIN_USER"
ADMIN_PASSWORD_ENV = "SENTINEL_ADMIN_PASSWORD"

# Sessions expire so a captured token cannot grant access forever; the
# operator can also end one early with POST /auth/logout.
SESSION_MAX_AGE_HOURS = 12


def _session_cutoff() -> str:
    """SQLite datetime modifier for the oldest still-valid session."""
    return f"-{SESSION_MAX_AGE_HOURS} hours"
_PBKDF2_ROUNDS = 240_000

# Failed-login throttle. The middleware in main.py already caps sign-in
# attempts per client IP; it is blind to the attack this one stops — a
# credential-stuffing run spread over many addresses, each politely under
# the per-IP limit, all of it aimed at one account. Counting *failures*
# per username closes that gap. In-process by deliberate choice: one
# uvicorn worker, no Redis in the locked dependency set, and a restart
# that forgets the counters costs the operator nothing an attacker gains.
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 300.0
# The keys are attacker-chosen, so the map is swept on every write and
# capped. When the cap bites it drops the entries whose last failure is
# oldest — those expire soonest anyway, so it costs the freshest evidence
# nothing.
LOGIN_FAILURE_MAX_TRACKED = 4096

_login_failures: dict[str, deque[float]] = {}


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8, max_length=200)
    role: str = "viewer"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    created_at: str


class LoginResponse(BaseModel):
    token: str
    user: UserOut


def _hash_password(password: str, salt: str) -> str:
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ROUNDS
    )
    return derived.hex()


def _to_user(row: sqlite3.Row) -> UserOut:
    return UserOut(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        created_at=row["created_at"],
    )


def _now() -> float:
    """Read the throttle's clock — one place, so a test can wind it forward.

    Monotonic rather than wall-clock: an NTP correction in the middle of a
    window must never hand an attacker a fresh budget of guesses.
    """
    return time.monotonic()


def _live_failures(username: str, now: float) -> deque[float]:
    """This username's failures still inside the window, oldest first."""
    hits = _login_failures.get(username)
    if hits is None:
        return deque()
    while hits and now - hits[0] > LOGIN_FAILURE_WINDOW_SECONDS:
        hits.popleft()
    if not hits:
        del _login_failures[username]
    return hits


def login_retry_after(username: str) -> int:
    """Seconds this username must wait, or 0 when the attempt may proceed.

    The number is the truth rather than a round constant: it is how long
    until the oldest failure slides out of the window and the budget
    reopens, so a client that waits exactly this long is never turned away
    a second time for the same reason.
    """
    now = _now()
    hits = _live_failures(username, now)
    if len(hits) < LOGIN_FAILURE_LIMIT:
        return 0
    return max(1, math.ceil(LOGIN_FAILURE_WINDOW_SECONDS - (now - hits[0])))


def _record_login_failure(username: str) -> None:
    """Remember one failed attempt against this username.

    Unknown usernames are counted too. Throttling only the accounts that
    exist would make the 429 itself an account-enumeration oracle — the
    one thing the constant-cost password check in ``login`` is there to
    avoid.
    """
    now = _now()
    _login_failures.setdefault(username, deque()).append(now)
    _forget_stale_failures(now)


def _forget_stale_failures(now: float) -> None:
    """Keep the throttle map bounded — nothing else ever shrinks it."""
    expired = [
        name
        for name, hits in _login_failures.items()
        if now - hits[-1] > LOGIN_FAILURE_WINDOW_SECONDS
    ]
    for name in expired:
        del _login_failures[name]
    excess = len(_login_failures) - LOGIN_FAILURE_MAX_TRACKED
    if excess > 0:
        by_age = sorted(_login_failures, key=lambda name: _login_failures[name][-1])
        for name in by_age[:excess]:
            del _login_failures[name]


def _clear_login_failures(username: str) -> None:
    """Forget this username's failures — the password just proved itself."""
    _login_failures.pop(username, None)


def reset_login_throttle() -> None:
    """Drop every remembered failure.

    The counters are process-global, like the feed cache and the telemetry
    buffer: a test (or a demo reset) needs a way back to a clean slate.
    """
    _login_failures.clear()


@router.post("/register", status_code=201, responses={409: {"description": "taken"}})
def register(
    body: RegisterRequest, conn: sqlite3.Connection = Depends(db.get_db)
) -> UserOut:
    """Create a local account — always the least-privileged 'viewer'.

    Self-service registration is anonymous, so it must never grant an elevated
    role: the requested role is validated for a clean error, but the account is
    always created as 'viewer'. Elevating a role is an admin-only action.
    """
    if body.role not in ROLES:
        raise HTTPException(
            status_code=422, detail=f"role must be one of {list(ROLES)}"
        )
    salt = secrets.token_hex(16)
    password_hash = _hash_password(body.password, salt)
    try:
        with db.writing(conn):
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, salt, role) "
                "VALUES (?, ?, ?, ?)",
                (body.username, password_hash, salt, "viewer"),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="username already taken") from None
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _to_user(row)


def invalidate_user_sessions(conn: sqlite3.Connection, user_id: int) -> int:
    """Revoke every session this account holds; returns how many died.

    The blunt instrument a privilege change needs. A session row records
    who signed in, never at what level, so the role is re-read from the
    users table on every request — which means a token minted while the
    account was a viewer would silently inherit an elevation, and one
    minted as an admin would keep working through a demotion. Cutting all
    of them forces a fresh sign-in, and the new token is then unambiguously
    a token for the new level. Call inside an open ``writing()``
    transaction.
    """
    return conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,)).rowcount


def purge_expired_sessions(conn: sqlite3.Connection) -> int:
    """Delete sessions past the window; returns how many were swept.

    Nothing else ever removes them: logout deletes the one row it was
    handed, and a client that simply closes the tab abandons its row for
    good. Expiry already makes those rows useless, so the only thing they
    accumulate is disk. Sweeping on each login — the one path that grows
    the table — keeps it proportional to the last 12 hours of sign-ins
    instead of to the lifetime of the deployment. The predicate is the
    exact complement of the one ``current_user`` accepts, so this can only
    delete rows that were already being refused.
    """
    return conn.execute(
        "DELETE FROM sessions WHERE created_at < datetime('now', ?)",
        (_session_cutoff(),),
    ).rowcount


@router.post(
    "/login",
    responses={
        401: {"description": "bad credentials"},
        429: {"description": "too many failed attempts"},
    },
)
def login(
    body: LoginRequest, conn: sqlite3.Connection = Depends(db.get_db)
) -> LoginResponse:
    """Verify credentials and issue an opaque session token."""
    # Answered before the deliberately expensive hash: being wrong must not
    # be a way to spend the server's CPU. A throttled attempt is never
    # counted either, so hammering a locked account cannot extend the
    # lockout — the window always drains, and a correct password is waiting
    # for its owner on the other side of it.
    retry_after = login_retry_after(body.username)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail=(
                "too many failed sign-in attempts for this account — "
                f"try again in {retry_after}s"
            ),
            headers={"Retry-After": str(retry_after)},
        )
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (body.username,)
    ).fetchone()
    # Always hash, even when the user is missing, so a wrong username and a
    # wrong password cost the same; compare_digest avoids a timing leak.
    salt = row["salt"] if row is not None else secrets.token_hex(16)
    candidate = _hash_password(body.password, salt)
    ok = row is not None and secrets.compare_digest(candidate, row["password_hash"])
    if not ok:
        _record_login_failure(body.username)
        raise HTTPException(status_code=401, detail="invalid username or password")
    _clear_login_failures(body.username)
    token = secrets.token_urlsafe(32)
    with db.writing(conn):
        purge_expired_sessions(conn)
        conn.execute(
            "INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, row["id"])
        )
    return LoginResponse(token=token, user=_to_user(row))


def _bearer_token(authorization: str | None) -> str:
    """Pull the token out of an Authorization header, or return ''."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return ""
    return authorization[7:].strip()


def _user_for_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    """Find the account behind a still-valid session token.

    One lookup for all three readers, so the expiry window can never be
    enforced in one of them and forgotten in another. The token is matched
    by primary key inside SQLite, so there is no Python-side comparison to
    make constant-time here; what guards it is its size — 32 bytes from
    ``secrets.token_urlsafe`` — and the window being part of the predicate
    rather than a check the caller is trusted to remember.
    """
    if not token:
        return None
    return conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ? AND s.created_at >= datetime('now', ?)",
        (token, _session_cutoff()),
    ).fetchone()


def current_user(
    authorization: str | None = Header(None),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> UserOut:
    """Resolve the bearer token to a user, or 401. Use as a dependency."""
    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    row = _user_for_token(conn, token)
    if row is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return _to_user(row)


def optional_user(
    authorization: str | None = Header(None),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> UserOut | None:
    """Like current_user but returns None instead of 401 when unauthenticated.

    Lets an endpoint derive a server-authenticated actor when a token is
    present and fall back to prior behaviour when it is not — additive, so
    existing unauthenticated flows are unchanged.
    """
    row = _user_for_token(conn, _bearer_token(authorization))
    return _to_user(row) if row is not None else None


def require_role(minimum: str):
    """Dependency factory: 403 unless the user is at least `minimum` role.

    The seam decision endpoints plug into (e.g. only an approver may approve).
    """
    order = {role: index for index, role in enumerate(ROLES)}

    def _dependency(user: UserOut = Depends(current_user)) -> UserOut:
        if order[user.role] < order[minimum]:
            raise HTTPException(
                status_code=403, detail=f"requires at least the {minimum} role"
            )
        return user

    return _dependency


def require_approver_enabled() -> bool:
    return os.environ.get(REQUIRE_APPROVER_ENV, "").strip() == "1"


def enforce_decision_auth(user: UserOut | None) -> None:
    """Gate a decision verb behind an approver+ session in live-ops mode.

    No-op when ``SENTINEL_REQUIRE_APPROVER`` is off — today's behavior stays
    identical. When on: a missing or invalid token answers 401; an
    authenticated session below 'approver' answers 403 naming the needed
    role. Reads stay public, and registration stays open — self-registration
    always creates a 'viewer', so strangers can look but never decide.
    """
    if not require_approver_enabled():
        return
    if user is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "operator mode: deciding requires a signed-in approver or "
                "admin session"
            ),
        )
    order = {role: index for index, role in enumerate(ROLES)}
    if order.get(user.role, -1) < order["approver"]:
        raise HTTPException(
            status_code=403,
            detail=(
                f"operator mode: the '{user.role}' role cannot decide — "
                "the approver or admin role is required"
            ),
        )


def _elevate_bootstrap_admin(
    conn: sqlite3.Connection, row: sqlite3.Row, username: str, password: str
) -> None:
    """Finish a half-done bootstrap: promote our own account, cut its tokens.

    Only an account that proves it is already ours is touched, and the proof
    is the configured password verifying against the stored hash in constant
    time. That distinction is the whole point: on a deploy whose disk
    survives a restart, the configured username may belong to a stranger who
    registered it first, and promoting them would hand away the estate. A
    mismatch is therefore left completely alone and merely noted.

    A promotion is a privilege change, so every session the account holds is
    revoked in the same transaction — a token issued to a viewer must not
    quietly become an admin's token. The next sign-in mints a new one at the
    new level, which is session rotation stated as what it actually is.
    """
    candidate = _hash_password(password, row["salt"])
    if not secrets.compare_digest(candidate, row["password_hash"]):
        logger.warning(
            "bootstrap admin %r already exists with a different password — "
            "left untouched",
            username,
        )
        return
    if row["role"] == "admin":
        return
    with db.writing(conn):
        conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (row["id"],))
        revoked = invalidate_user_sessions(conn, row["id"])
    logger.info(
        "bootstrap admin %r elevated from %r, %d session(s) revoked",
        username,
        row["role"],
        revoked,
    )


def ensure_bootstrap_admin() -> None:
    """Lifespan hook: create the env-configured deciding account, once.

    ``SENTINEL_ADMIN_USER`` + ``SENTINEL_ADMIN_PASSWORD`` seed one 'admin'
    account on an ephemeral-disk deploy — the schema rebuilds from nothing
    on every boot, so the team's operator identity must too. Idempotent and
    non-destructive: a stored password is never rewritten (a boot can never
    reset one), and the password itself is never logged. The one thing a
    boot may still finish is the elevation — see
    ``_elevate_bootstrap_admin``, which acts only on an account that
    verifies as ours.
    """
    username = os.environ.get(ADMIN_USER_ENV, "").strip()
    password = os.environ.get(ADMIN_PASSWORD_ENV, "")
    if not username or not password:
        return
    conn = db.connect_ready()
    try:
        existing = conn.execute(
            "SELECT id, role, salt, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if existing is not None:
            _elevate_bootstrap_admin(conn, existing, username, password)
            return
        salt = secrets.token_hex(16)
        password_hash = _hash_password(password, salt)
        try:
            with db.writing(conn):
                conn.execute(
                    "INSERT INTO users (username, password_hash, salt, role) "
                    "VALUES (?, ?, ?, 'admin')",
                    (username, password_hash, salt),
                )
        except sqlite3.IntegrityError:
            return  # raced by a concurrent boot: the first writer wins
        logger.info("bootstrap admin %r is ready to decide", username)
    finally:
        conn.close()


@router.get("/me", responses={401: {"description": "not authenticated"}})
def me(user: UserOut = Depends(current_user)) -> UserOut:
    """Return the authenticated user."""
    return user


@router.post("/logout")
def logout(
    authorization: str | None = Header(None),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> dict:
    """End the current session — the token stops working immediately.

    Idempotent and quiet: an absent or already-invalid token still returns
    200, so a client can always clear its state without branching. Only the
    presented session ends — signing out of one browser must not knock the
    same operator out of the console they left open on the wall screen.
    """
    token = _bearer_token(authorization)
    if token:
        with db.writing(conn):
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    return {"detail": "signed out"}
