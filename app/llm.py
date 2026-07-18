"""LLM provider layer for CloudSentinel agents.

Design constraints:
- Classic ``generate_content`` + ``response_schema`` path of google-genai;
  parsed Pydantic output comes back on ``response.parsed``.
- Manual retry around 429s because the SDK's built-in retry ignores the
  server-requested ``retryDelay`` (python-genai issue #1875).
- When the quota is exhausted the caller can fall back to a rule-based
  answer via ``generate_with_fallback``; the result is tagged so the UI
  can show a "(fallback)" badge.
- ``SENTINEL_FAKE_LLM=1`` (or a missing API key) selects a deterministic
  fake provider so tests, CI and quota-less environments keep working.
- Response schemas must not declare field defaults: Gemini's
  ``response_schema`` translation silently drops them.
"""

import contextvars
import json
import logging
import math
import os
import re
import time
import types
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Literal, Union, get_args, get_origin

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field

try:  # allow fake-only environments without the SDK installed
    from google import genai
except ImportError:  # pragma: no cover
    genai = None

logger = logging.getLogger("cloudsentinel.llm")

# Quota-starved backup, if ever needed: gemini-2.5-flash-lite.
DEFAULT_MODEL = "gemini-2.5-flash"

MAX_ATTEMPTS = 4
TRANSIENT_STATUS = {500, 502, 503, 504}
TRANSIENT_BACKOFF_SECONDS = 2.0
DEFAULT_429_DELAY_SECONDS = 30.0
# A longer server ask means the daily quota is gone; waiting is pointless.
MAX_RETRY_DELAY_SECONDS = 120.0

DATA_DELIMITER_OPEN = "<<untrusted-data>>"
DATA_DELIMITER_CLOSE = "<</untrusted-data>>"

# Hard per-call transport timeout for the live provider (Sprint 3, S3-⑤):
# a hung request must fail into the retry/fallback machinery, not wedge a
# worker thread indefinitely.
LLM_TIMEOUT_ENV = "SENTINEL_LLM_TIMEOUT_SECONDS"
DEFAULT_LLM_TIMEOUT_SECONDS = 30.0


def llm_timeout_seconds() -> float:
    raw = os.environ.get(LLM_TIMEOUT_ENV, "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_LLM_TIMEOUT_SECONDS
    if math.isfinite(value) and value > 0:
        return value
    return DEFAULT_LLM_TIMEOUT_SECONDS


class Confidence(BaseModel):
    """Self-assessed confidence attached to agent output."""

    score: float = Field(ge=0.0, le=1.0, description="Confidence between 0 and 1.")
    rationale: str = Field(description="One-sentence justification for the score.")


class LLMUnavailableError(Exception):
    """No live completion could be obtained within the retry budget."""


# --- per-scope call budget (Sprint 3, S3-⑤) ---------------------------------
#
# A budget caps how many provider calls one logical unit of work (a pulse,
# a demo run) may spend. Overruns raise LLMUnavailableError, which lands on
# the SAME fallback paths quota exhaustion already uses: agents degrade to
# their rule-based answers instead of failing, and the budget object records
# that it ran dry so reports can say so honestly.


@dataclass
class CallBudget:
    """Mutable counter for provider calls inside one budget scope."""

    limit: int
    used: int = 0
    exhausted: bool = False


_call_budget: contextvars.ContextVar[CallBudget | None] = contextvars.ContextVar(
    "sentinel_llm_call_budget", default=None
)


@contextmanager
def llm_call_budget(limit: int):
    """Cap provider calls inside this scope; yields the live counter."""
    budget = CallBudget(limit=limit)
    token = _call_budget.set(budget)
    try:
        yield budget
    finally:
        _call_budget.reset(token)


def charge_call_budget() -> None:
    """Consume one call from the active budget scope, if any.

    Raises LLMUnavailableError once the scope is dry — callers already
    treat that as "no live answer" and fall back deterministically.
    """
    budget = _call_budget.get()
    if budget is None:
        return
    if budget.used >= budget.limit:
        budget.exhausted = True
        raise LLMUnavailableError(
            f"llm call budget exhausted ({budget.limit} calls in this scope)"
        )
    budget.used += 1


@dataclass
class LLMResult:
    """Outcome of a generation call, tagged with where it came from."""

    text: str
    parsed: BaseModel | None
    source: Literal["gemini", "fake", "fallback"]
    model: str


class LLMProvider(ABC):
    """Common interface every completion backend implements."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResult:
        """Return a completion for ``prompt``.

        Raises LLMUnavailableError when no answer can be produced within
        the retry budget (rate limit / transient outage).
        """


def wrap_untrusted(data: str) -> str:
    """Wrap untrusted payload in spotlighting delimiters (arXiv:2403.14720).

    Delimiter tokens are stripped from the payload first (repeatedly, so
    stripping cannot reassemble a new delimiter) to keep the payload from
    breaking out of its data section.
    """
    cleaned = data
    # Joint fixed point: removing one token can splice a new instance of the
    # other together, so keep going until neither survives. Terminates because
    # every pass with a token present strictly shrinks the string.
    while DATA_DELIMITER_OPEN in cleaned or DATA_DELIMITER_CLOSE in cleaned:
        cleaned = cleaned.replace(DATA_DELIMITER_OPEN, "")
        cleaned = cleaned.replace(DATA_DELIMITER_CLOSE, "")
    return f"{DATA_DELIMITER_OPEN}\n{cleaned}\n{DATA_DELIMITER_CLOSE}"


def _retry_delay_seconds(err: Exception) -> float | None:
    """Extract the server-requested delay from a 429 error.

    Primary path: ``google.rpc.RetryInfo`` inside ``err.details``; falls
    back to a regex over the error string. Returns None when the server
    gave no usable hint.
    """
    details = getattr(err, "details", None) or []
    if isinstance(details, dict):
        error_body = details.get("error")
        details = error_body.get("details") if isinstance(error_body, dict) else None
    if isinstance(details, list):
        for item in details:
            if not isinstance(item, dict):
                continue
            if not str(item.get("@type", "")).endswith("google.rpc.RetryInfo"):
                continue
            match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(item.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    match = re.search(r"retryDelay[\"':\s]*(\d+(?:\.\d+)?)s?", str(err))
    return float(match.group(1)) if match else None


class GeminiProvider(LLMProvider):
    """Live Gemini backend with manual, retryDelay-aware retry."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        sleep: Callable[[float], None] = time.sleep,
        client: object | None = None,
    ):
        if client is None:
            if genai is None:
                raise RuntimeError(
                    "google-genai is not installed; install it or set SENTINEL_FAKE_LLM=1"
                )
            key = api_key or os.environ.get("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("GEMINI_API_KEY is not set")
            # Hard transport timeout (milliseconds): a hung call fails into
            # the transient-retry path instead of blocking a worker thread.
            client = genai.Client(
                api_key=key,
                http_options={"timeout": int(llm_timeout_seconds() * 1000)},
            )
        self._client = client
        self._model = model
        self._max_attempts = max_attempts
        self._sleep = sleep

    @property
    def model(self) -> str:
        """Model id used for quota accounting and cache keying."""
        return self._model

    def generate(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResult:
        charge_call_budget()
        config: dict = {}
        if system_instruction is not None:
            config["system_instruction"] = system_instruction
        if response_schema is not None:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = response_schema

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.models.generate_content(
                    model=self._model, contents=prompt, config=config or None
                )
            except Exception as err:  # status decides; unknown errors re-raise
                status = getattr(err, "code", None)
                # httpx transport errors (connect/read timeouts, DNS blips)
                # carry no status code but are just as transient as a 5xx.
                transient = status in TRANSIENT_STATUS or (
                    status is None and isinstance(err, httpx.TransportError)
                )
                if status == 429:
                    last_error = err
                    delay = _retry_delay_seconds(err)
                    if delay is None:
                        delay = DEFAULT_429_DELAY_SECONDS
                    if delay > MAX_RETRY_DELAY_SECONDS:
                        raise LLMUnavailableError(
                            f"rate limited; server asked to wait {delay:.0f}s "
                            "which signals an exhausted daily quota"
                        ) from err
                    if attempt == self._max_attempts:
                        break
                    logger.warning(
                        "429 from Gemini, honoring retryDelay=%.1fs (attempt %d/%d)",
                        delay, attempt, self._max_attempts,
                    )
                    self._sleep(delay)
                elif transient:
                    last_error = err
                    if attempt == self._max_attempts:
                        break
                    delay = TRANSIENT_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    logger.warning(
                        "transient %s from Gemini, backing off %.1fs (attempt %d/%d)",
                        status or type(err).__name__, delay, attempt, self._max_attempts,
                    )
                    self._sleep(delay)
                else:
                    raise
            else:
                parsed = getattr(response, "parsed", None)
                if response_schema is not None and parsed is None:
                    raise LLMUnavailableError(
                        "Gemini returned no parsed payload for the requested schema"
                    )
                return LLMResult(
                    text=response.text or "",
                    parsed=parsed,
                    source="gemini",
                    model=self._model,
                )
        raise LLMUnavailableError(
            f"no completion after {self._max_attempts} attempts"
        ) from last_error


# --- context-aware fake output ----------------------------------------------
#
# The generic fake payload fills every string with "fake" — structurally
# valid, but a demo (or a jury-day quota outage) then shows lifeless cards.
# Agent modules may register a COMPOSER for their response schema: it
# receives the prompt's first untrusted-data payload (parsed JSON) and
# returns a realistic, deterministic payload dict. Composers must keep the
# generic path's structural values (triage class, confidence score) so the
# fake lane stays behaviorally identical — only the narrative gains life.
# _example_payload remains the fallback for unregistered schemas.

FakeComposer = Callable[[dict], dict]
_fake_composers: dict[str, FakeComposer] = {}


def register_fake_composer(schema: type[BaseModel], composer: FakeComposer) -> None:
    _fake_composers[schema.__name__] = composer


def extract_untrusted_payload(prompt: str) -> dict:
    """First untrusted-data block of the prompt as a dict ({} on any miss)."""
    start = prompt.find(DATA_DELIMITER_OPEN)
    if start < 0:
        return {}
    start += len(DATA_DELIMITER_OPEN)
    end = prompt.find(DATA_DELIMITER_CLOSE, start)
    if end < 0:
        return {}
    try:
        parsed = json.loads(prompt[start:end].strip())
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _example_value(annotation) -> object:
    """Produce a deterministic sample value for a schema field."""
    origin = get_origin(annotation)
    if origin is Literal:
        return get_args(annotation)[0]
    if origin in (list, set, tuple):
        return []
    if origin in (Union, types.UnionType):  # Optional[X] and PEP 604 "X | None"
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        return _example_value(non_none[0]) if non_none else None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _example_payload(annotation)
    samples = {str: "fake", float: 0.5, int: 0, bool: False}
    if annotation in samples:
        return samples[annotation]
    raise ValueError(f"FakeProvider cannot invent a value for {annotation!r}")


def _example_payload(schema: type[BaseModel]) -> dict:
    return {
        name: _example_value(field.annotation)
        for name, field in schema.model_fields.items()
    }


class FakeProvider(LLMProvider):
    """Deterministic provider for tests, CI and quota-less environments."""

    def __init__(self, canned_text: str = "fake response", canned_payload: dict | None = None):
        self._canned_text = canned_text
        self._canned_payload = canned_payload

    @property
    def model(self) -> str:
        """Model id used for quota accounting and cache keying."""
        return "fake"

    def generate(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResult:
        charge_call_budget()  # the budget is observable under the fake too
        parsed = None
        if response_schema is not None:
            payload = self._canned_payload
            if payload is None:
                composer = _fake_composers.get(response_schema.__name__)
                if composer is not None:
                    try:
                        payload = composer(extract_untrusted_payload(prompt))
                    except Exception:
                        logger.warning(
                            "fake composer for %s failed; using the generic payload",
                            response_schema.__name__,
                            exc_info=True,
                        )
                        payload = None
            if payload is None:
                payload = _example_payload(response_schema)
            parsed = response_schema.model_validate(payload)
        return LLMResult(text=self._canned_text, parsed=parsed, source="fake", model="fake")


def generate_with_fallback(
    provider: LLMProvider,
    prompt: str,
    *,
    fallback: Callable[[], tuple[str, BaseModel | None]],
    system_instruction: str | None = None,
    response_schema: type[BaseModel] | None = None,
) -> LLMResult:
    """Try the provider; on quota exhaustion return the rule-based answer.

    ``fallback`` is a zero-argument callable computing the deterministic
    answer; its result is tagged ``source="fallback"`` so the UI can show
    the "(fallback)" badge.
    """
    try:
        return provider.generate(
            prompt,
            system_instruction=system_instruction,
            response_schema=response_schema,
        )
    except LLMUnavailableError:
        logger.warning("LLM unavailable, serving rule-based fallback")
        text, parsed = fallback()
        return LLMResult(text=text, parsed=parsed, source="fallback", model="rule-based")


def provider_mode() -> str:
    """Cheap answer to "which backend would answer right now" (fake|gemini).

    Mirrors get_provider's selection WITHOUT instantiating a client or
    logging — health checks call this on every ping.
    """
    load_dotenv()
    flag = os.environ.get("SENTINEL_FAKE_LLM", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return "fake"
    if not os.environ.get("GEMINI_API_KEY"):
        return "fake"
    return "gemini"


def get_provider() -> LLMProvider:
    """Pick the provider from the environment.

    Any truthy ``SENTINEL_FAKE_LLM`` value (from the shell or ``.env``)
    forces the fake; a missing ``GEMINI_API_KEY`` also falls back to it
    (with a warning) so the app degrades instead of crashing. The live
    provider announces itself in the log so nobody burns the shared
    daily quota without noticing.
    """
    load_dotenv()  # never overrides variables already set in the shell
    flag = os.environ.get("SENTINEL_FAKE_LLM", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return FakeProvider()
    if flag:
        logger.warning(
            "SENTINEL_FAKE_LLM=%r is not a recognized truthy value; "
            "treating it as OFF (use 1/true/yes/on to force the fake provider)",
            flag,
        )
    if not os.environ.get("GEMINI_API_KEY"):
        logger.warning("GEMINI_API_KEY not set, using FakeProvider")
        return FakeProvider()
    logger.warning(
        "using LIVE GeminiProvider (model=%s) — requests count against the "
        "shared free-tier daily quota",
        DEFAULT_MODEL,
    )
    return GeminiProvider()
