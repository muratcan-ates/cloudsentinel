"""Mission DSL — YAML mission definitions, validated hard (Sprint 3, S3-①).

A mission is the declarative description of one watch: what to detect,
with which detector and thresholds, under which organizational intent,
and when to escalate to debate. Files live in ``configs/<name>.yaml``,
are parsed with ``yaml.safe_load`` (never ``load`` — config is data,
not code) and validated by Pydantic before anything runs. A malformed
mission raises at load time instead of silently running with defaults.

The mission name is a filename component, so it is allow-listed to a
strict slug — a path traversal can never reach outside ``configs/``.
"""

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

CONFIG_DIR = Path(__file__).parent.parent / "configs"
DEFAULT_MISSION = "finops"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class MissionError(ValueError):
    """A mission file is missing, unparseable or fails validation."""


class MissionDetection(BaseModel):
    source: Literal["cost", "security", "fraud"]
    threshold: float = Field(gt=0)
    critical_z: float = Field(gt=0)
    detector: Literal["zscore", "mad"]
    baseline_window_days: int = Field(ge=7)
    seasonal: bool


class MissionEscalation(BaseModel):
    confidence_debate_threshold: float = Field(ge=0.0, le=1.0)


class MissionConfig(BaseModel):
    mission: str
    title: str
    description: str
    organizational_intent: str
    role_intent: dict[str, str]
    detection: MissionDetection
    escalation: MissionEscalation


_cache: dict[str, MissionConfig] = {}


def clear_mission_cache() -> None:
    """Test hook: force the next ``get_mission`` to re-read the file."""
    _cache.clear()


def load_mission(name: str) -> MissionConfig:
    """Read and validate ``configs/<name>.yaml`` (uncached)."""
    if not _NAME_RE.fullmatch(name):
        raise MissionError(f"invalid mission name: {name!r}")
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.is_file():
        raise MissionError(f"mission file not found: {path.name}")
    # Every load failure must surface as MissionError — the endpoint
    # fallbacks catch exactly that, and a bad encoding or a permission
    # problem must degrade the same way a bad schema does.
    try:
        source = path.read_text()
    except (OSError, UnicodeDecodeError) as error:
        raise MissionError(f"mission {name}: unreadable file — {error}") from error
    try:
        raw = yaml.safe_load(source)
    except yaml.YAMLError as error:
        raise MissionError(f"mission {name}: unparseable YAML — {error}") from error
    if not isinstance(raw, dict):
        raise MissionError(f"mission {name}: top level must be a mapping")
    try:
        config = MissionConfig.model_validate(raw)
    except ValidationError as error:
        raise MissionError(f"mission {name}: {error}") from error
    if config.mission != name:
        raise MissionError(
            f"mission {name}: file declares mission {config.mission!r}"
        )
    return config


def get_mission(name: str = DEFAULT_MISSION) -> MissionConfig:
    """Cached mission lookup — configs are immutable within a process."""
    if name not in _cache:
        _cache[name] = load_mission(name)
    return _cache[name]
