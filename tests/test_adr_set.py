"""The decision record is a shipped artifact, so it is checked like one.

These are cheap link-and-claim tests, not prose review. They exist because
a broken relative link or an ADR that stops matching the code it describes
is exactly the kind of rot nobody notices until a reviewer does.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
ADR_DIR = ROOT / "docs" / "adr"
SECURITY = ROOT / "SECURITY.md"

ADRS = sorted(path for path in ADR_DIR.glob("[0-9]*.md"))


def _links(text: str) -> list[str]:
    """Relative markdown link targets, anchors and externals dropped."""
    targets = re.findall(r"\]\(([^)]+)\)", text)
    return [
        target
        for target in targets
        if not target.startswith(("http://", "https://", "#", "mailto:"))
    ]


def test_the_recorded_decisions_are_all_present():
    """Four decisions a reader would otherwise have to guess at."""
    assert len(ADRS) >= 5
    names = " ".join(path.name for path in ADRS)
    for topic in ("sqlite", "agent-framework", "model-allowlist", "simulated", "fake"):
        assert topic in names, f"no ADR covers {topic}"


@pytest.mark.parametrize("adr", ADRS, ids=lambda path: path.stem)
def test_each_adr_states_its_status_and_its_cost(adr):
    """An ADR that only lists benefits is marketing, not a decision record."""
    text = adr.read_text(encoding="utf-8")
    assert "**Status:**" in text
    assert "## Consequences" in text or "## Decision" in text
    assert "rejected" in text.lower(), "no alternatives were weighed"


@pytest.mark.parametrize("doc", [*ADRS, ADR_DIR / "README.md", SECURITY],
                         ids=lambda path: path.name)
def test_every_relative_link_resolves(doc):
    for target in _links(doc.read_text(encoding="utf-8")):
        resolved = (doc.parent / target.split("#")[0]).resolve()
        assert resolved.exists(), f"{doc.name} links to a missing {target}"


def test_the_index_lists_every_adr():
    index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    for adr in ADRS:
        assert adr.name in index, f"{adr.name} is missing from the ADR index"


def test_security_policy_names_a_reporting_channel_and_a_timeline():
    text = SECURITY.read_text(encoding="utf-8")
    assert "Report a vulnerability" in text
    assert "muratcn.ates@gmail.com" in text
    assert "do not open a public issue" in text.lower()
    assert "## Scope" in text
    assert re.search(r"within \d+ days", text), "no response commitment stated"


def test_security_policy_disowns_the_hostname_that_is_not_ours():
    """A stranger's app holds cloudsentinel.onrender.com; a researcher must
    not be pointed at it."""
    text = SECURITY.read_text(encoding="utf-8")
    assert "cloudsentinel-y5zh.onrender.com" in text
    assert "is not ours" in text


def test_the_allowlist_adr_still_matches_the_code():
    """The one ADR that names a code symbol must not outlive it."""
    from app import llm

    text = (ADR_DIR / "0003-model-allowlist.md").read_text(encoding="utf-8")
    assert "ALLOWED_MODELS" in text
    assert hasattr(llm, "ALLOWED_MODELS")
    assert hasattr(llm, "assert_allowlisted")
