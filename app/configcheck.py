"""Boot-time configuration audit — a production profile that refuses to lie.

Every safety property of this app is an environment variable: the
read-only guard on the public link, the approver requirement on the
decision desk, the deterministic provider, the outbound guard's
developer escape hatch. Each is off by default, which is right for a
laptop and wrong for a deployment — and a missing variable fails
*silently*, which is the worst of both.

So the profile decides how loud a gap is:

    SENTINEL_ENV=production   every finding is fatal — the app refuses
                              to boot rather than serve a demo posture
                              to real users
    anything else             the same findings are logged as [CONFIG]
                              warnings; behavior is unchanged

Today's deployment runs ``SENTINEL_ENV=render`` and is deliberately a
read-only showcase, so it keeps booting exactly as before. The strict
profile is the door the product walks through when there is a first
real user — the check exists before the deployment that needs it,
which is the only order that ever works.
"""

import os

PRODUCTION_ENVS = frozenset({"production", "prod"})

READONLY_ENV = "SENTINEL_READONLY"
REQUIRE_APPROVER_ENV = "SENTINEL_REQUIRE_APPROVER"
ADMIN_USER_ENV = "SENTINEL_ADMIN_USER"
ADMIN_PASSWORD_ENV = "SENTINEL_ADMIN_PASSWORD"
FAKE_LLM_ENV = "SENTINEL_FAKE_LLM"
ALLOW_FAKE_ENV = "SENTINEL_ALLOW_FAKE_PROVIDER"
ALLOW_PRIVATE_TARGETS_ENV = "SENTINEL_ALLOW_PRIVATE_TARGETS"
MIN_ADMIN_PASSWORD_LENGTH = 12


def app_env() -> str:
    return os.environ.get("SENTINEL_ENV", "local").strip().lower()


def is_production(env: str | None = None) -> bool:
    return (env if env is not None else app_env()) in PRODUCTION_ENVS


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


def findings() -> list[str]:
    """Every configuration gap that matters for a real deployment.

    Ordered by consequence. Each line names the fix, because a boot
    refusal that does not say what to set is just a longer outage.
    """
    gaps: list[str] = []

    if not _flag(READONLY_ENV) and not _flag(REQUIRE_APPROVER_ENV):
        gaps.append(
            "writes are open to anyone: set SENTINEL_READONLY=1 for a "
            "showcase, or SENTINEL_REQUIRE_APPROVER=1 to require an "
            "authenticated approver"
        )

    if _flag(REQUIRE_APPROVER_ENV):
        username = os.environ.get(ADMIN_USER_ENV, "").strip()
        password = os.environ.get(ADMIN_PASSWORD_ENV, "")
        if not username or not password:
            gaps.append(
                "approvals are required but no bootstrap admin exists: set "
                "SENTINEL_ADMIN_USER and SENTINEL_ADMIN_PASSWORD, or nobody "
                "can approve anything"
            )
        elif len(password) < MIN_ADMIN_PASSWORD_LENGTH:
            gaps.append(
                "the bootstrap admin password is shorter than "
                f"{MIN_ADMIN_PASSWORD_LENGTH} characters"
            )

    if _fake_provider_selected() and not _flag(ALLOW_FAKE_ENV):
        gaps.append(
            "the deterministic fake provider would serve real users: "
            "configure GEMINI_API_KEY with SENTINEL_FAKE_LLM=0, or set "
            "SENTINEL_ALLOW_FAKE_PROVIDER=1 to accept it deliberately"
        )

    if _flag(ALLOW_PRIVATE_TARGETS_ENV):
        gaps.append(
            "the outbound guard's developer escape hatch is open: unset "
            "SENTINEL_ALLOW_PRIVATE_TARGETS so feeds and webhooks cannot "
            "be aimed at private addresses"
        )

    return gaps


def _fake_provider_selected() -> bool:
    """True when the deterministic provider would answer, key or not."""
    flag = os.environ.get(FAKE_LLM_ENV, "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    return not os.environ.get("GEMINI_API_KEY", "").strip()


class UnsafeConfiguration(RuntimeError):
    """The production profile was asked to boot with a demo posture."""


def enforce_boot() -> list[str]:
    """Audit at boot: fatal under the production profile, loud otherwise.

    Returns the findings so the caller can log them; raises only when
    ``SENTINEL_ENV`` names a production profile.
    """
    gaps = findings()
    if gaps and is_production():
        listed = "\n".join(f"  - {gap}" for gap in gaps)
        raise UnsafeConfiguration(
            "SENTINEL_ENV=production refuses this configuration:\n" + listed
        )
    return gaps
