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

import logging
import os
import re
import time
import types
from abc import ABC, abstractmethod
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


class Confidence(BaseModel):
    """Self-assessed confidence attached to agent output."""

    score: float = Field(ge=0.0, le=1.0, description="Confidence between 0 and 1.")
    rationale: str = Field(description="One-sentence justification for the score.")


class LLMUnavailableError(Exception):
    """No live completion could be obtained within the retry budget."""


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
            client = genai.Client(api_key=key)
        self._client = client
        self._model = model
        self._max_attempts = max_attempts
        self._sleep = sleep

    def generate(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResult:
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

    def generate(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResult:
        parsed = None
        if response_schema is not None:
            payload = (
                self._canned_payload
                if self._canned_payload is not None
                else _example_payload(response_schema)
            )
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


def get_provider() -> LLMProvider:
    """Pick the provider from the environment.

    ``SENTINEL_FAKE_LLM=1`` (from the shell or ``.env``) forces the fake;
    a missing ``GEMINI_API_KEY`` also falls back to it (with a warning) so
    the app degrades instead of crashing.
    """
    load_dotenv()  # never overrides variables already set in the shell
    if os.environ.get("SENTINEL_FAKE_LLM") == "1":
        return FakeProvider()
    if not os.environ.get("GEMINI_API_KEY"):
        logger.warning("GEMINI_API_KEY not set, using FakeProvider")
        return FakeProvider()
    return GeminiProvider()
