#!/usr/bin/env bash
# CloudSentinel smoke test — exercises the live chain end to end and prints
# a PASS/FAIL table. Works against a local server or a deployed base URL:
#
#   bash scripts/smoke.sh                       # http://127.0.0.1:8000
#   bash scripts/smoke.sh https://<host>        # deployed instance
#
# curl + python3 only; read-only deployments will (correctly) fail the two
# POST steps — that is the knob working, not the product breaking.
set -u

BASE="${1:-http://127.0.0.1:8000}"
pass=0
fail=0

check() {
  local name="$1" expected="$2" method="$3" path="$4"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" -X "$method" --max-time 30 "$BASE$path")
  if [ "$code" = "$expected" ]; then
    printf 'PASS  %-38s %s %s\n' "$name" "$method" "$path"
    pass=$((pass + 1))
  else
    printf 'FAIL  %-38s %s %s -> %s (expected %s)\n' "$name" "$method" "$path" "$code" "$expected"
    fail=$((fail + 1))
  fi
}

json_field() { # json_field <path> <python-expr over body as b>
  curl -s --max-time 30 "$BASE$1" | python3 -c "import json,sys; b=json.load(sys.stdin); print($2)"
}

echo "CloudSentinel smoke — $BASE"
echo "--------------------------------------------------------------"
check "health answers"                 200 GET  /health
check "anomaly scan"                   200 GET  /anomalies
check "cost summary"                   200 GET  /costs/summary
check "security lane"                  200 GET  /security/signals
check "fraud lane"                     200 GET  /fraud/signals
check "pulse chain"                    200 POST /pulse
check "last pulse persisted"           200 GET  /pulse/last
check "inbox lists proposals"          200 GET  /actions
check "decision ledger export"         200 GET  /decisions/export
check "analytics funnel"               200 GET  /analytics/decisions
check "self-finops ledger"             200 GET  /analytics/ai
check "api docs (self-hosted)"         200 GET  /docs
check "unknown action 404s"            404 POST /actions/999999/approve
echo "--------------------------------------------------------------"
echo "provider: $(json_field /health "b['provider']") · env: $(json_field /health "b['env']") · version: $(json_field /health "b['version']")"
echo "result: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
