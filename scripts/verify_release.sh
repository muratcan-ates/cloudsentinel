#!/usr/bin/env bash
# CloudSentinel release verification — measure the claims instead of trusting
# them. Every counter in the README is a promise to a juror who can check it;
# this script checks it first.
#
#   bash scripts/verify_release.sh                    # counters + links
#   bash scripts/verify_release.sh https://<host>     # ...plus the live link
#
# What it does:
#   1. counts the test suite (pytest --collect-only) and the API surface
#      (the app's own OpenAPI paths) — the real numbers, not the written ones;
#   2. reads every "N tests / N endpoints" claim out of the shipped docs and
#      fails on any that disagrees;
#   3. follows every relative markdown link and fails on the dead ones;
#   4. against a live URL: confirms it is OUR app answering (a stranger's
#      service once squatted a very similar hostname), healthy, and still
#      serving the security headers.
#
# Historical claims — "446 tests over 46 endpoints at Sprint 2 close" — are
# records, not promises, and are left alone. A claim counts as historical when
# it sits under a Sprint 1/2 heading, says so on its own line, or carries an
# explicit `<!-- verify-release: historical -->` marker. The heuristics are
# printed, never silent: VERBOSE=1 shows every claim and how it was judged, and
# a present-tense claim that happens to name a sprint can be forced back into
# the checked set with `<!-- verify-release: live -->`.
#
# python3 + curl only, no network unless a URL is given.
set -u

cd "$(dirname "$0")/.." || exit 2

PYTHON="${PYTHON:-.venv/bin/python}"
PYTEST="${PYTEST:-.venv/bin/pytest}"
[ -x "$PYTHON" ] || PYTHON=python3
[ -x "$PYTEST" ] || PYTEST="$PYTHON -m pytest"

BASE="${1:-}"
pass=0
fail=0

ok()   { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf 'FAIL  %s\n' "$1"; fail=$((fail + 1)); }
note() { printf '      %s\n' "$1"; }

echo "CloudSentinel release verification"
echo "=============================================================="

# --- 1. measure what is actually there ---------------------------------------

echo
echo "-- measuring --"

TEST_COUNT=$(SENTINEL_FAKE_LLM=1 $PYTEST --collect-only -q 2>/dev/null |
  sed -n 's/^\([0-9][0-9]*\) tests* collected.*/\1/p' | tail -1)
if [ -z "${TEST_COUNT:-}" ]; then
  bad "could not count the test suite (pytest --collect-only produced no total)"
  TEST_COUNT=0
else
  ok "test suite collects $TEST_COUNT cases"
fi

ENDPOINT_COUNT=$(SENTINEL_FAKE_LLM=1 $PYTHON -c '
import sys
sys.stderr = open("/dev/null", "w")
from main import app
print(len(app.openapi()["paths"]))
' 2>/dev/null | tail -1)
if [ -z "${ENDPOINT_COUNT:-}" ]; then
  bad "could not count the API surface (the app would not import)"
  ENDPOINT_COUNT=0
else
  ok "API surface exposes $ENDPOINT_COUNT paths"
fi

# --- 2. the written claims must match ----------------------------------------

echo
echo "-- counter claims --"

CLAIM_REPORT=$(TEST_COUNT="$TEST_COUNT" ENDPOINT_COUNT="$ENDPOINT_COUNT" $PYTHON - <<'PY'
import os
import re
import sys
from pathlib import Path

TESTS = int(os.environ["TEST_COUNT"])
ENDPOINTS = int(os.environ["ENDPOINT_COUNT"])

# Where a claim is a live promise rather than a sprint record.
DOCS = ["README.md", "docs/README.tr.md", "docs/CLOSEOUT_48H.md", "PROJECT_CONTEXT.md"]

# "580 pytest cases", "51 endpoints", "580 test" (tr), "32 uç nokta" (tr).
CLAIM = re.compile(
    r"(?P<number>\d[\d,]*)\s*"
    r"(?P<unit>pytest cases|tests|test|endpoints|endpoint|uç nokta)\b",
    re.IGNORECASE,
)
UNIT_KIND = {
    "pytest cases": "tests",
    "tests": "tests",
    "test": "tests",
    "endpoints": "endpoints",
    "endpoint": "endpoints",
    "uç nokta": "endpoints",
}
EXPECTED = {"tests": TESTS, "endpoints": ENDPOINTS}

# A record of a past sprint, not a promise about this build.
HISTORICAL_LINE = re.compile(
    r"sprint\s*[12]\b|sprint close|kapanışında|verify-release:\s*historical",
    re.IGNORECASE,
)
HISTORICAL_HEADING = re.compile(r"^#{1,3}\s.*sprint\s*[12]\b", re.IGNORECASE)
# The override for the awkward middle case: a present-tense claim that also
# mentions the sprint it landed in. `<!-- verify-release: live -->` on the
# line forces it to be checked, whatever the heuristics above decide.
FORCE_LIVE = re.compile(r"verify-release:\s*live", re.IGNORECASE)
HEADING = re.compile(r"^#{1,6}\s")

# Prose that happens to contain the words, e.g. "a single anomaly-detection
# endpoint" or "1 test". Only claims that read as a count are checked.
SKIP_CONTEXT = re.compile(r"\b(single|one|each|per|the)\s+\w*\s*$", re.IGNORECASE)

problems = 0
checked = 0
skipped = 0

for name in DOCS:
    path = Path(name)
    if not path.exists():
        print(f"MISS  {name} is referenced by the release checklist but absent")
        problems += 1
        continue
    heading = ""
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if HEADING.match(line):
            heading = line
        for match in CLAIM.finditer(line):
            number = int(match.group("number").replace(",", ""))
            kind = UNIT_KIND[match.group("unit").lower()]
            before = line[: match.start()]
            if SKIP_CONTEXT.search(before):
                continue
            historical = not FORCE_LIVE.search(line) and bool(
                HISTORICAL_LINE.search(line) or HISTORICAL_HEADING.match(heading)
            )
            where = f"{name}:{lineno}"
            if historical:
                skipped += 1
                print(f"hist  {where}  {number} {kind} (sprint record — not checked)")
                continue
            checked += 1
            if number == EXPECTED[kind]:
                print(f"ok    {where}  {number} {kind}")
            else:
                problems += 1
                print(
                    f"BAD   {where}  claims {number} {kind}, "
                    f"reality is {EXPECTED[kind]}"
                )
                print(f"        {line.strip()[:110]}")

print(f"SUMMARY {checked} live claims checked, {skipped} sprint records skipped")
sys.exit(1 if problems else 0)
PY
)
CLAIM_STATUS=$?
echo "$CLAIM_REPORT" | grep -E '^(BAD|MISS)' >/dev/null && echo "$CLAIM_REPORT" | grep -vE '^(ok|hist)' || echo "$CLAIM_REPORT" | grep -E '^SUMMARY'
if [ "$CLAIM_STATUS" -eq 0 ]; then
  ok "every live counter claim matches the code"
else
  bad "a shipped document claims a number the code does not back"
  note "run with VERBOSE=1 to see every claim, historical ones included"
fi
[ "${VERBOSE:-0}" = "1" ] && echo "$CLAIM_REPORT"

# --- 3. relative links must resolve ------------------------------------------

echo
echo "-- markdown links --"

LINK_REPORT=$($PYTHON - <<'PY'
import re
import sys
from pathlib import Path

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SKIP = re.compile(r"^(https?:|mailto:|#|data:|tel:)")

roots = [Path(".")] + [Path("docs")]
files = sorted({p for root in roots for p in root.glob("*.md")})

dead = 0
checked = 0
for path in files:
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for target in LINK.findall(line):
            if SKIP.match(target):
                continue
            # Drop the anchor: a heading fragment is not a file.
            clean = target.split("#", 1)[0]
            if not clean:
                continue
            checked += 1
            if not (path.parent / clean).exists():
                dead += 1
                print(f"DEAD  {path}:{lineno}  -> {target}")

print(f"SUMMARY {checked} relative links across {len(files)} files, {dead} dead")
sys.exit(1 if dead else 0)
PY
)
LINK_STATUS=$?
echo "$LINK_REPORT" | grep -E '^(DEAD|SUMMARY)'
if [ "$LINK_STATUS" -eq 0 ]; then
  ok "every relative markdown link resolves"
else
  bad "a shipped document links to something that is not there"
fi

# --- 4. the live link, if one was given --------------------------------------

if [ -n "$BASE" ]; then
  echo
  echo "-- live link: $BASE --"

  # The free tier sleeps; a cold start can take ~46 s and a short timeout
  # reads as "the link is dead" when it is merely asleep.
  HEALTH=$(curl -s --max-time 90 "$BASE/health")
  if [ -z "$HEALTH" ]; then
    bad "no answer from $BASE/health within 90 s"
  else
    STATUS=$(printf '%s' "$HEALTH" |
      $PYTHON -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' 2>/dev/null)
    if [ "$STATUS" = "ok" ]; then
      ok "live /health answers ok"
      note "$(printf '%s' "$HEALTH" | $PYTHON -c 'import json,sys; b=json.load(sys.stdin); print("version %s · env %s · provider %s · readonly %s" % (b.get("version"), b.get("env"), b.get("provider"), b.get("readonly")))' 2>/dev/null)"
    else
      bad "live /health did not answer ok"
    fi
  fi

  # Identity: a stranger's app has held a very similar hostname before, and a
  # 200 from the wrong service is worse than a 500 from the right one.
  TITLE=$(curl -s --max-time 60 "$BASE/openapi.json" |
    $PYTHON -c 'import json,sys; print(json.load(sys.stdin).get("info",{}).get("title",""))' 2>/dev/null)
  if [ "$TITLE" = "CloudSentinel API" ]; then
    ok "the host is serving OUR app (openapi title: $TITLE)"
  else
    bad "openapi title is '${TITLE:-<none>}', not 'CloudSentinel API' — wrong service on this host"
  fi

  # The deployed surface must match the code being submitted.
  LIVE_PATHS=$(curl -s --max-time 60 "$BASE/openapi.json" |
    $PYTHON -c 'import json,sys; print(len(json.load(sys.stdin).get("paths",{})))' 2>/dev/null)
  if [ "${LIVE_PATHS:-0}" = "$ENDPOINT_COUNT" ]; then
    ok "live surface matches this checkout ($LIVE_PATHS paths)"
  else
    bad "live serves ${LIVE_PATHS:-?} paths, this checkout has $ENDPOINT_COUNT — the deploy is behind"
  fi

  HEADERS=$(curl -s -D - -o /dev/null --max-time 60 "$BASE/health" | tr 'A-Z' 'a-z')
  missing=""
  for header in content-security-policy x-content-type-options x-frame-options referrer-policy; do
    printf '%s' "$HEADERS" | grep -q "^$header:" || missing="$missing $header"
  done
  if [ -z "$missing" ]; then
    ok "security headers present on the live response"
  else
    bad "live response is missing:$missing"
  fi

  # The watch is the thing that quietly stopped once already.
  WATCH=$(curl -s --max-time 60 "$BASE/ops/health/watch" |
    $PYTHON -c 'import json,sys; b=json.load(sys.stdin); print(b.get("state",""), b.get("detail",""))' 2>/dev/null)
  case "$WATCH" in
    healthy*|starting*|off*) ok "standing watch: $WATCH" ;;
    "") note "live instance predates /ops/health/watch — skipping the watch check" ;;
    *) bad "standing watch: $WATCH" ;;
  esac
fi

# --- verdict -----------------------------------------------------------------

echo
echo "=============================================================="
echo "result: $pass passed, $fail failed"
if [ "$fail" -ne 0 ]; then
  echo "Do not tag a release while a claim is unbacked — a juror who checks"
  echo "one number and finds it wrong stops believing the rest."
fi
[ "$fail" -eq 0 ]
