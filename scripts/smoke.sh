#!/usr/bin/env bash
# CloudSentinel smoke test — exercises the live chain end to end and prints
# a PASS/FAIL table. Works against a local server or a deployed base URL:
#
#   bash scripts/smoke.sh                       # http://127.0.0.1:8000
#   bash scripts/smoke.sh https://<host>        # deployed instance
#
# curl + python3 only. Read-only showcase deploys block every write with
# 403 BEFORE routing — there the guard firing IS the pass, and /pulse/last
# may honestly be 404 until the watchdog's first beat.
set -u

BASE="${1:-http://127.0.0.1:8000}"
pass=0
fail=0

READONLY=$(curl -s --max-time 30 "$BASE/health" | python3 -c "import json,sys; print(json.load(sys.stdin).get('readonly', False))" 2>/dev/null || echo False)

check() { # expected may be a space-separated list of acceptable codes
  local name="$1" expected="$2" method="$3" path="$4"
  local code ok=""
  code=$(curl -s -o /dev/null -w "%{http_code}" -X "$method" --max-time 30 "$BASE$path")
  case " $expected " in *" $code "*) ok=1;; esac
  if [ -n "$ok" ]; then
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
if [ "$READONLY" = "True" ]; then
  check "pulse chain (readonly guard)"   403 POST /pulse
  check "last pulse persisted"           "200 404" GET /pulse/last
else
  check "pulse chain"                    200 POST /pulse
  check "last pulse persisted"           200 GET  /pulse/last
fi
check "inbox lists proposals"          200 GET  /actions
check "decision ledger export"         200 GET  /decisions/export
check "analytics funnel"               200 GET  /analytics/decisions
check "self-finops ledger"             200 GET  /analytics/ai
check "market watch"                   200 GET  /market/opportunities
check "api docs (self-hosted)"         200 GET  /docs
if [ "$READONLY" = "True" ]; then
  check "write guard holds"              403 POST /actions/999999/approve
else
  check "unknown action 404s"            404 POST /actions/999999/approve
fi
echo "--------------------------------------------------------------"
echo "provider: $(json_field /health "b['provider']") · env: $(json_field /health "b['env']") · version: $(json_field /health "b['version']")"
echo "result: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
