"""Tagged log lines — one helper for the chain's machine-readable trail.

Every hop of the pipeline announces itself with a bracketed tag and a
sorted-JSON payload ([REFLEX], [SIGNAL], [ANALYST], [DEBATE],
[RECOMMENDER], [HITL], ...), so a single mock spike can be followed end
to end in the server output — and parsed by anything that splits the
message on its first space. This module is that one sentence's grammar:
no app imports, no state beyond the request id, just the format every
agent speaks.

Two things were missing for that trail to hold up outside a demo.

**Correlation.** The tags said *what* happened, never *which request* it
happened for; two operators clicking at once interleave into one
unreadable stream. The request id the HTTP layer already mints now rides
a context variable, so every ``log_tag`` call — all of them, unchanged
at the call site — carries ``request_id`` automatically, and the same
value is on the caller's ``X-Request-ID`` response header. One pulse can
be followed from the click to the last agent hop.

**Machine-readability.** ``[TAG] {json}`` is kind to a human watching a
demo and awkward for a log pipeline, which wants one object per line
with level and timestamp included. ``SENTINEL_LOG_FORMAT=json`` switches
the whole stream to exactly that. It is opt-in: with the knob unset the
output is byte-identical to before, because the demo reads those
bracketed lines out loud and no submission-day surprise is worth a log
format.
"""

import contextvars
import json
import logging
import os

LOG_FORMAT_ENV = "SENTINEL_LOG_FORMAT"
REQUEST_ID_FIELD = "request_id"

# The id of the request being served on this task, or None outside one
# (the watchdog's own pulses, tests, a script). A ContextVar rather than
# a thread local: the endpoints are a mix of async and threadpool work,
# and only a context variable follows both.
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cloudsentinel_request_id", default=None
)


def set_request_id(request_id: str | None):
    """Bind the current request id; returns the token to reset with."""
    return _request_id.set(request_id)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def current_request_id() -> str | None:
    return _request_id.get()


def log_tag(logger: logging.Logger, tag: str, **fields) -> None:
    """Emit one ``[TAG] {json}`` line — sorted keys, split-on-first-space safe.

    The active request id joins the payload when there is one, so a
    caller can grep their own trail out of a busy stream. An explicit
    ``request_id`` field always wins over the ambient one.
    """
    request_id = current_request_id()
    if request_id is not None:
        fields.setdefault(REQUEST_ID_FIELD, request_id)
    payload = json.dumps(fields, sort_keys=True)
    logger.info("%s %s", tag, payload, extra={"cloudsentinel_tag": tag})


class JsonFormatter(logging.Formatter):
    """One JSON object per line: level, logger, tag, fields, request id.

    A tagged line is re-expanded — the ``[TAG] {json}`` payload becomes
    real fields instead of a string the pipeline would have to parse a
    second time. Anything else (uvicorn's own lines, a stray warning)
    still comes through with its message intact, because a log format
    that drops the lines it does not recognise is worse than none.
    """

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        entry: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
        }
        tag = getattr(record, "cloudsentinel_tag", None)
        fields = None
        if tag and message.startswith(tag):
            candidate = message[len(tag) :].strip()
            try:
                parsed = json.loads(candidate)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                fields = parsed
        if fields is not None:
            entry["tag"] = tag.strip("[]")
            entry["fields"] = fields
            request_id = fields.get(REQUEST_ID_FIELD)
            if request_id is not None:
                entry[REQUEST_ID_FIELD] = request_id
        else:
            entry["message"] = message
            ambient = current_request_id()
            if ambient is not None:
                entry[REQUEST_ID_FIELD] = ambient
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, sort_keys=True, default=str)


def json_logging_requested() -> bool:
    return os.environ.get(LOG_FORMAT_ENV, "").strip().lower() == "json"


def install_json_logging(force: bool = False) -> bool:
    """Reformat every configured handler as JSON. No-op unless asked.

    Returns whether the switch happened, so the boot manifest can say so
    plainly instead of leaving the operator to infer it from the output.
    """
    if not force and not json_logging_requested():
        return False
    formatter = JsonFormatter()
    root = logging.getLogger()
    handlers = list(root.handlers)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "cloudsentinel"):
        handlers.extend(logging.getLogger(name).handlers)
    if not handlers:  # nothing configured yet — give the root one
        handler = logging.StreamHandler()
        root.addHandler(handler)
        handlers = [handler]
    for handler in handlers:
        handler.setFormatter(formatter)
    return True
