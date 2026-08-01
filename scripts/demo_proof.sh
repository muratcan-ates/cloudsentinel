#!/usr/bin/env bash
#
# demo_proof.sh — the closing beat of the demo.
#
# The live walkthrough shows the product working once. This shows that the
# guarantees behind it hold every time, in six lines a juror can read in four
# seconds. Each line runs a real subset of the existing suite; nothing here
# is a hardcoded verdict. A group with no tests prints "not covered" rather
# than a pass it did not earn, and any failure exits non-zero.
#
#   bash scripts/demo_proof.sh          # or: make demo-proof
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 4

# --- the guarantees ----------------------------------------------------------
# "name|pytest arguments". The arguments are eval'd (our own literals) so a
# -k expression can carry spaces. Every entry selects existing tests by node
# id or by -k over the files that own the behaviour. (Not named GROUPS: bash
# reserves that array for the caller's unix groups and ignores the assignment.)
GUARANTEES=(
  "health checks|tests/test_watchdog.py tests/test_ops_pack.py tests/test_demo_ops.py tests/test_cost_summary.py -k 'health or ready'"
  "anomaly detection|tests/test_anomalies.py tests/test_detection_quality.py tests/test_detection_properties.py"
  "recommendation lifecycle|tests/test_analyst.py::test_scan_then_analyze_end_to_end tests/test_recommender.py::test_recommend_requires_prior_analysis tests/test_recommender.py::test_recommendation_files_a_proposed_action tests/test_recommender.py::test_second_recommend_reuses_the_open_action tests/test_actions.py::test_full_lifecycle_proposed_to_executed tests/test_analytics.py::test_funnel_counts_follow_the_lifecycle"
  "decision persistence|tests/test_db.py::test_restart_persistence tests/test_actions.py::test_decision_survives_in_database tests/test_actions.py::test_decisions_and_executions_land_on_the_trail tests/test_decisions.py::test_operator_decisions_land_in_memory tests/test_decisions.py::test_reject_without_rationale_leaves_no_memory tests/test_analyst.py::test_analysis_is_persisted_on_the_event"
  "read-only protection|tests/test_endpoint_matrix.py tests/test_auth.py tests/test_demo_ops.py tests/test_operator_mode.py tests/test_llm_contracts.py tests/test_stream.py -k 'readonly'"
  "concurrent decision safety|tests/test_actions.py tests/test_db.py tests/test_recommender.py tests/test_dispatch.py -k '(concurrent or idempoten or conflict) and not init_db and not cors'"
)

# --- the runner --------------------------------------------------------------
# The venv by locked decision; a git worktree does not carry one, so fall back
# to the main checkout's before giving up on PATH.
resolve_pytest() {
  local main
  [ -n "${PYTEST:-}" ] && { printf '%s\n' "$PYTEST"; return 0; }
  [ -x "$ROOT/.venv/bin/pytest" ] && { printf '%s\n' "$ROOT/.venv/bin/pytest"; return 0; }
  main=$(git -C "$ROOT" worktree list 2>/dev/null | head -1 | awk '{print $1}')
  [ -n "$main" ] && [ -x "$main/.venv/bin/pytest" ] && { printf '%s\n' "$main/.venv/bin/pytest"; return 0; }
  command -v pytest 2>/dev/null
}

PYTEST_BIN=$(resolve_pytest)
if [ -z "$PYTEST_BIN" ]; then
  echo "demo_proof: no pytest found — run 'make setup' first (or set PYTEST=)." >&2
  exit 4
fi

# Colour only for a human at a terminal; a redirected log stays plain.
if [ -t 1 ]; then
  DIM=$'\033[2m'; GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
else
  DIM=''; GREEN=''; RED=''; YELLOW=''; OFF=''
fi

LOG_DIR=$(mktemp -d "${TMPDIR:-/tmp}/demo_proof.XXXXXX") || exit 4
trap 'rm -rf "$LOG_DIR"' EXIT

# Last "<n> <word>" in the pytest summary, or 0.
tally() {
  local n
  n=$(grep -Eo "[0-9]+ $2" "$1" | tail -1 | cut -d' ' -f1)
  printf '%s\n' "${n:-0}"
}

total_tests=0
total_failed=0
held=0
covered=0
failed_lines=()
SECONDS=0

printf '\n  %sCloudSentinel — guarantees under proof%s\n\n' "$DIM" "$OFF"

for entry in "${GUARANTEES[@]}"; do
  name=${entry%%|*}
  args=${entry#*|}
  log="$LOG_DIR/$(echo "$name" | tr ' ' '_').log"

  eval "set -- $args"
  SENTINEL_FAKE_LLM=1 "$PYTEST_BIN" -q --tb=short -rf --disable-warnings \
    -p no:cacheprovider "$@" >"$log" 2>&1
  rc=$?

  passed=$(tally "$log" passed)
  failed=$(( $(tally "$log" failed) + $(tally "$log" error) + $(tally "$log" errors) ))
  ran=$(( passed + failed ))
  total_tests=$(( total_tests + ran ))
  total_failed=$(( total_failed + failed ))

  if [ "$rc" -eq 5 ]; then
    verdict='not covered'; colour=$YELLOW; count='no tests select this'
  elif [ "$rc" -eq 0 ]; then
    verdict='passed'; colour=$GREEN; count=$(printf '%3d tests' "$ran")
    held=$(( held + 1 )); covered=$(( covered + 1 ))
  else
    verdict='FAIL'; colour=$RED; count="${failed} of ${ran} failing"
    covered=$(( covered + 1 )); failed_lines+=("$name|$log")
  fi

  # colour is printed around the padded field, never inside it, so the
  # columns stay square whether or not this is a terminal
  printf '  %-28s%s%-13s%s%s%s%s\n' \
    "$name" "$colour" "$verdict" "$OFF" "$DIM" "$count" "$OFF"
done

noun=failures; [ "$total_failed" -eq 1 ] && noun=failure
printf '\n  %s of %s guarantees hold — %s tests, %s %s, %ss\n\n' \
  "$held" "$covered" "$total_tests" "$total_failed" "$noun" "$SECONDS"

# A proof that cannot fail is not a proof: name the broken tests and say so
# with the exit status, not just the colour.
if [ ${#failed_lines[@]} -gt 0 ]; then
  for entry in "${failed_lines[@]}"; do
    printf '  %sbroken — %s%s\n' "$RED" "${entry%%|*}" "$OFF"
    grep -E '^(FAILED|ERROR) ' "${entry#*|}" | sed 's/^/    /' | head -20
  done
  printf '\n'
  exit 1
fi

exit 0
