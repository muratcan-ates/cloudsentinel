"""Mission DSL — YAML mission definitions, validated hard (Sprint 3, S3-①).

A mission is the declarative description of one watch: what to detect,
with which detector and thresholds, under which organizational intent,
and when to escalate to debate. Files live in ``configs/<name>.yaml``,
are parsed as data — a ``SafeLoader`` subclass, the tag set that cannot
construct a Python object — and validated by Pydantic before anything
runs.

The loader is deliberately unforgiving, because for a config file every
alternative to raising is a lie the system tells later:

* an unknown key is a knob that does nothing: ``leave_one_out`` copied
  across from the environment layer, or a setting that outlived the
  schema, sitting in the file looking live while no scan ever reads it;
* a duplicate key is silently won by the last one, so the line an
  operator reads is not the setting in force;
* a lax type coercion (``"2.0"`` for a number, ``"no"`` for a boolean)
  hides a broken template or an environment substitution that never
  happened, so strict mode is on and a config means what it says;
* a value the detector cannot use is silently replaced at scan time —
  ``run_detection`` substitutes its own default for a baseline window
  below ``MIN_HISTORY`` — so the number in the file never runs and
  nothing anywhere says so;
* a shadowing file (``finops.yml`` beside ``finops.yaml``, or
  ``FinOps.yaml`` on a case-insensitive laptop) is the file you edit
  without it ever being the file that loads.

All of them refuse to load, and the message names the file, the key and
the range that would have been accepted — a refusal that does not say
what to fix is just a longer outage.

The mission name is a filename component, so it is allow-listed to a
strict slug — a path traversal can never reach outside ``configs/``.
"""

import os
import re
from pathlib import Path
from typing import Annotated, Literal, get_args, get_origin

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)
from pydantic.fields import FieldInfo

from app.detection import MIN_HISTORY

CONFIG_DIR = Path(__file__).parent.parent / "configs"
DEFAULT_MISSION = "finops"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# Ceilings for the two z-score knobs and the baseline window. They are
# typo catchers, not opinions about statistics: a slipped decimal
# (2.0 -> 2000.0) mutes a lane forever and looks healthy while it does
# it, and a "rolling" baseline longer than a year is not rolling. They
# also make every numeric range in this DSL finite, so an error message
# can state the whole accepted interval instead of half of one.
MAX_Z_SCORE = 100.0
MAX_WINDOW_DAYS = 365

# Refuse anything the detector would quietly override. run_detection
# falls back to its own default window below MIN_HISTORY, so a smaller
# number here would never be the number in force.
MIN_WINDOW_DAYS = MIN_HISTORY

# Strict: no key we did not declare, and no cross-type coercion. Both
# halves matter — a rejected key catches the typo, strict types catch
# the quoted number that a lax parser would turn into the value the
# author only appears to have written.
_STRICT = ConfigDict(strict=True, extra="forbid")


class MissionError(ValueError):
    """A mission file is missing, unparseable or fails validation."""


def _not_blank(value: str) -> str:
    """A present-but-blank string is a missing one wearing a disguise."""
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _slug(value: str) -> str:
    if not _NAME_RE.fullmatch(value):
        raise ValueError(f"{value!r} does not match {_NAME_RE.pattern}")
    return value


# Free text is where a mission states what it is for. Nothing downstream
# reads these fields back, so a blank title or intent would sail past
# every check the app has and simply read as an empty mission — which
# makes the loader the one place it can still be caught.
_Text = Annotated[str, AfterValidator(_not_blank)]
_Slug = Annotated[str, AfterValidator(_slug)]


class MissionDetection(BaseModel):
    model_config = _STRICT

    source: Literal["cost", "security", "fraud"]
    threshold: float = Field(gt=0.0, le=MAX_Z_SCORE)
    critical_z: float = Field(gt=0.0, le=MAX_Z_SCORE)
    detector: Literal["zscore", "mad"]
    baseline_window_days: int = Field(ge=MIN_WINDOW_DAYS, le=MAX_WINDOW_DAYS)
    seasonal: bool

    @model_validator(mode="after")
    def severity_bands_must_be_ordered(self) -> "MissionDetection":
        if self.critical_z < self.threshold:
            raise ValueError(
                f"critical_z ({self.critical_z}) must be at least threshold "
                f"({self.threshold}); below it every flagged signal is "
                "critical and the warning band cannot happen"
            )
        return self


class MissionEscalation(BaseModel):
    model_config = _STRICT

    confidence_debate_threshold: float = Field(ge=0.0, le=1.0)


class FraudRules(BaseModel):
    """Tunable knobs of the published deterministic rule score.

    Only thresholds live here — the point values stay code constants in
    app/fraud.py so the score arithmetic remains a published, auditable
    contract. Still zero statistics, zero generation.
    """

    model_config = _STRICT

    hold_band: int = Field(ge=1, le=100)
    review_band: int = Field(ge=1, le=100)
    new_account_days: int = Field(ge=1)

    @model_validator(mode="after")
    def bands_must_be_ordered(self) -> "FraudRules":
        if self.review_band >= self.hold_band:
            raise ValueError(
                f"review_band ({self.review_band}) must sit below "
                f"hold_band ({self.hold_band})"
            )
        return self


class MissionConfig(BaseModel):
    model_config = _STRICT

    # Validated as a slug here too, not only against the filename: this
    # model is importable, and a caller who validates a dict directly
    # deserves the same guarantee the file loader gets.
    mission: _Slug
    title: _Text
    description: _Text
    organizational_intent: _Text
    role_intent: dict[str, _Text] = Field(min_length=1)
    detection: MissionDetection
    escalation: MissionEscalation
    # Fraud-lane only: rule-score thresholds. Optional so the cost and
    # security missions stay untouched.
    rules: FraudRules | None = None

    @model_validator(mode="after")
    def rules_belong_to_the_fraud_lane(self) -> "MissionConfig":
        # Only app/fraud.py reads this block, and only from the fraud
        # mission. Anywhere else it is decoration that changes nothing;
        # missing from the fraud lane it hands scoring back to the code
        # constants while the file looks like it is in charge.
        fraud_lane = self.detection.source == "fraud"
        if fraud_lane and self.rules is None:
            raise ValueError(
                "a fraud mission must carry a rules block; without it the "
                "score silently falls back to the built-in bands"
            )
        if self.rules is not None and not fraud_lane:
            raise ValueError(
                f"rules is a fraud-lane block, but detection.source is "
                f"{self.detection.source!r}; nothing would ever read it"
            )
        return self


# Nested models by field name, derived from MissionConfig rather than
# restated, so an error message cannot describe a shape that changed.
_SECTIONS: dict[str, type[BaseModel]] = {
    field_name: candidate
    for field_name, field in MissionConfig.model_fields.items()
    for candidate in (field.annotation, *get_args(field.annotation))
    if isinstance(candidate, type) and issubclass(candidate, BaseModel)
}

_BOUNDS = ("gt", "ge", "lt", "le")


def _constraint(field: FieldInfo, name: str):
    """A constraint the field declares (``gt``, ``min_length``, …), or None.

    Read by attribute rather than by importing the constraint classes, so
    this stays independent of which library supplies them.
    """
    for meta in field.metadata:
        value = getattr(meta, name, None)
        if value is not None:
            return value
    return None


def _owner_of(loc: tuple) -> type[BaseModel] | None:
    """The model that declares the key an error points at.

    The DSL is two levels deep by design; anything deeper (a role_intent
    value, say) has no declared field of its own and falls back to
    Pydantic's own wording.
    """
    if len(loc) == 1:
        return MissionConfig
    if len(loc) == 2:
        return _SECTIONS.get(str(loc[0]))
    return None


def _field_of(loc: tuple) -> FieldInfo | None:
    owner = _owner_of(loc)
    return owner.model_fields.get(str(loc[-1])) if owner else None


def _accepted(field: FieldInfo) -> str | None:
    """The domain a field declares, phrased for whoever edits the file.

    Read off the model itself instead of being restated next to it, so
    the message can never advertise a range the schema stopped enforcing.
    """
    annotation = field.annotation
    if get_origin(annotation) is Literal:
        return "one of: " + ", ".join(str(value) for value in get_args(annotation))
    if annotation is bool:
        return "true or false"
    validators = {getattr(meta, "func", None) for meta in field.metadata}
    if annotation is str:
        if _slug in validators:
            return f"a slug matching {_NAME_RE.pattern}"
        if _not_blank in validators:
            return "text that is not blank"
        return "text"
    if get_origin(annotation) is dict:
        least = _constraint(field, "min_length")
        return "a mapping" if least is None else f"a mapping with {least} entry or more"
    if annotation not in (int, float):
        return None
    bounds = {
        bound: value
        for bound in _BOUNDS
        if (value := _constraint(field, bound)) is not None
    }
    noun = "an integer" if annotation is int else "a number"
    low = bounds.get("gt", bounds.get("ge"))
    high = bounds.get("lt", bounds.get("le"))
    if low is not None and high is not None:
        opening = "(" if "gt" in bounds else "["
        closing = ")" if "lt" in bounds else "]"
        return f"{noun} in {opening}{low}, {high}{closing}"
    if low is not None:
        return f"{noun} {'>' if 'gt' in bounds else '>='} {low}"
    if high is not None:
        return f"{noun} {'<' if 'lt' in bounds else '<='} {high}"
    return noun


def _explain(error: dict) -> str:
    """One validation failure, as a line an operator can act on."""
    location = ".".join(str(part) for part in error["loc"])
    if error["type"] == "extra_forbidden":
        owner = _owner_of(error["loc"])
        if owner is None:
            return f"{location} — unknown key"
        parent = ".".join(str(part) for part in error["loc"][:-1]) or "the mission"
        return (
            f"{location} — unknown key; {parent} accepts: "
            f"{', '.join(owner.model_fields)}"
        )
    if error["type"] == "value_error":
        # A cross-field rule already explains itself in a sentence; the
        # input is the whole block, so printing it would only add noise.
        message = error["msg"].removeprefix("Value error, ")
        return f"{location or 'the mission'} — {message}"
    field = _field_of(error["loc"])
    accepted = _accepted(field) if field is not None else None
    if error["type"] == "missing":
        required = f"{location} — required"
        return f"{required}, accepts {accepted}" if accepted else required
    if accepted is None:
        detail = error["msg"][:1].lower() + error["msg"][1:]
        return f"{location} — {detail}, got {error['input']!r}"
    return f"{location} — accepts {accepted}, got {error['input']!r}"


def _validation_message(where: str, error: ValidationError) -> str:
    lines = "\n".join(
        f"  - {_explain(item)}" for item in error.errors(include_url=False)
    )
    return f"{where} is invalid:\n{lines}"


class _DuplicateKey(yaml.YAMLError):
    """One mapping carries the same key twice."""


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate keys instead of keeping the last.

    Still a SafeLoader — no tag may construct a Python object — with the
    single addition that YAML's last-one-wins rule for a repeated key is
    treated as the config bug it always is.
    """

    def construct_mapping(self, node, deep=False):
        seen: set = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
            except TypeError:  # unhashable key: SafeLoader itself reports it
                continue
            if duplicate:
                raise _DuplicateKey(
                    f"duplicate key {key!r} on line {key_node.start_mark.line + 1}"
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _where(name: str) -> str:
    """The file an error is about, named the way an operator would."""
    return f"mission {name} ({CONFIG_DIR.name}/{name}.yaml)"


def _shadowing_files(name: str) -> list[str]:
    """Entries in the config directory that pose as ``<name>.yaml``.

    Only the exact filename is ever read, so a ``.yml`` twin or a file
    differing from it in case is a config you can edit forever with no
    effect. Worse, a case-insensitive filesystem (a macOS laptop) may
    load the twin while the Linux deployment reports the mission
    missing — the same repository, two behaviors. Refuse both rather
    than pick a winner.
    """
    wanted = f"{name}.yaml"
    twins = (wanted, f"{name}.yml")
    try:
        entries = sorted(entry.name for entry in CONFIG_DIR.iterdir())
    except OSError:  # no config directory: the caller reports "not found"
        return []
    return [entry for entry in entries if entry != wanted and entry.lower() in twins]


_cache: dict[str, MissionConfig] = {}

# Live quick-switch (in-memory, process-local): the dashboard flips the
# active mission through POST /pulse?mission=… and every no-arg
# get_mission() caller — /anomalies, /ready, the debate threshold —
# follows together. Lane-pinned callers (security/fraud) pass explicit
# names and never follow. Resolution: explicit arg > in-memory override
# > SENTINEL_MISSION env (ops boot default) > code default.
_active_mission: str | None = None
MISSION_ENV = "SENTINEL_MISSION"


def clear_mission_cache() -> None:
    """Test hook: force the next ``get_mission`` to re-read the file."""
    global _active_mission
    _cache.clear()
    _active_mission = None


def set_active_mission(name: str) -> "MissionConfig":
    """Validate and flip the process-wide active mission (quick-switch)."""
    global _active_mission
    config = load_mission(name)  # MissionError on unknown/invalid slugs
    _active_mission = name
    return config


def load_mission(name: str) -> MissionConfig:
    """Read and validate ``configs/<name>.yaml`` (uncached).

    Every way this can fail — missing, shadowed, unreadable, unparseable,
    duplicated, invalid — surfaces as MissionError, because the endpoint
    fallbacks catch exactly that and a bad encoding must degrade the same
    way a bad schema does.
    """
    if not _NAME_RE.fullmatch(name):
        raise MissionError(
            f"invalid mission name: {name!r} — expected a slug matching "
            f"{_NAME_RE.pattern}"
        )
    where = _where(name)
    shadows = _shadowing_files(name)
    if shadows:
        raise MissionError(
            f"{where}: shadowed by {', '.join(shadows)} — only {name}.yaml is "
            "ever read, so a case- or extension-twin is a file you can edit "
            "with no effect (and on a case-insensitive filesystem it may be "
            "the one that loads); rename or remove it"
        )
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.is_file():
        raise MissionError(f"{where}: file not found")
    try:
        source = path.read_text()
    except (OSError, UnicodeDecodeError) as error:
        raise MissionError(f"{where}: unreadable file — {error}") from error
    try:
        raw = yaml.load(source, Loader=_StrictLoader)
    except _DuplicateKey as error:
        raise MissionError(
            f"{where}: {error} — YAML keeps only the last one, so the setting "
            "you read would not be the setting in force"
        ) from error
    except yaml.YAMLError as error:
        raise MissionError(f"{where}: unparseable YAML — {error}") from error
    if not isinstance(raw, dict):
        raise MissionError(
            f"{where}: top level must be a mapping, got {type(raw).__name__}"
        )
    try:
        config = MissionConfig.model_validate(raw)
    except ValidationError as error:
        raise MissionError(_validation_message(where, error)) from error
    if config.mission != name:
        raise MissionError(
            f"{where}: file declares mission {config.mission!r} — the declared "
            "slug and the filename must match, or two files claim one mission"
        )
    return config


def get_mission(name: str | None = None) -> MissionConfig:
    """Cached mission lookup — configs are immutable within a process.

    A no-arg call resolves the ACTIVE mission (quick-switch override,
    then the env boot default, then finops); an explicit name always
    wins, so the lane-pinned callers never follow the switch.
    """
    if name is None:
        name = (
            _active_mission
            or os.environ.get(MISSION_ENV, "").strip()
            or DEFAULT_MISSION
        )
    if name not in _cache:
        _cache[name] = load_mission(name)
    return _cache[name]
