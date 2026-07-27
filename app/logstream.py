"""Tagged log lines — one helper for the chain's machine-readable trail.

Every hop of the pipeline announces itself with a bracketed tag and a
sorted-JSON payload ([REFLEX], [SIGNAL], [ANALYST], [DEBATE],
[RECOMMENDER], [HITL], ...), so a single mock spike can be followed end
to end in the server output — and parsed by anything that splits the
message on its first space. This module is that one sentence's grammar:
no app imports, no state, just the format every agent speaks.
"""

import json
import logging


def log_tag(logger: logging.Logger, tag: str, **fields) -> None:
    """Emit one ``[TAG] {json}`` line — sorted keys, split-on-first-space safe."""
    logger.info("%s %s", tag, json.dumps(fields, sort_keys=True))
