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
"""

import hashlib
import secrets
import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db

router = APIRouter(prefix="/auth", tags=["auth"])

ROLES = ("viewer", "analyst", "approver", "admin")
_PBKDF2_ROUNDS = 240_000


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


@router.post("/register", status_code=201, responses={409: {"description": "taken"}})
def register(
    body: RegisterRequest, conn: sqlite3.Connection = Depends(db.get_db)
) -> UserOut:
    """Create a local account. Role defaults to the least-privileged viewer."""
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
                (body.username, password_hash, salt, body.role),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="username already taken") from None
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _to_user(row)


@router.post("/login", responses={401: {"description": "bad credentials"}})
def login(
    body: LoginRequest, conn: sqlite3.Connection = Depends(db.get_db)
) -> LoginResponse:
    """Verify credentials and issue an opaque session token."""
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (body.username,)
    ).fetchone()
    # Always hash, even when the user is missing, so a wrong username and a
    # wrong password cost the same; compare_digest avoids a timing leak.
    salt = row["salt"] if row is not None else secrets.token_hex(16)
    candidate = _hash_password(body.password, salt)
    ok = row is not None and secrets.compare_digest(candidate, row["password_hash"])
    if not ok:
        raise HTTPException(status_code=401, detail="invalid username or password")
    token = secrets.token_urlsafe(32)
    with db.writing(conn):
        conn.execute(
            "INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, row["id"])
        )
    return LoginResponse(token=token, user=_to_user(row))


def current_user(
    authorization: str | None = Header(None),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> UserOut:
    """Resolve the bearer token to a user, or 401. Use as a dependency."""
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ?",
        (token,),
    ).fetchone()
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
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ?",
        (token,),
    ).fetchone()
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


@router.get("/me", responses={401: {"description": "not authenticated"}})
def me(user: UserOut = Depends(current_user)) -> UserOut:
    """Return the authenticated user."""
    return user
