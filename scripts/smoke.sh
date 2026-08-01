#!/usr/bin/env bash
# CloudSentinel smoke test — exercises the live chain end to end and prints
# a PASS/FAIL table. Works against a local server or a deployed base URL:
#
#   bash scripts/smoke.sh                       # http://127.0.0.1:8000
#   bash scripts/smoke.sh https://<host>        # deployed instance
#
# curl + python3 only. Read-only showcase deploys block every write with
# 403 BEFORE routing — there the guard firing IS the pass, and /pulse/last
# may honestly be 404 until the watchdog's first beat. Every write-dependent
# step branches on that, so the same script is meaningful against the public
# link and against a local stage.
#
# Some steps assert on the ANSWER, not just the status code: an endpoint that
# returns 200 with the wrong body is exactly the failure a status-only sweep
# sleeps through. A SKIP line is a step that does not apply here (writes
# disabled, nothing in the inbox, a surface this build does not ship) — it
# never counts as a pass.
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

post_json() { # post_json <path> <json-body> -> response body on stdout
  curl -s --max-time 30 -X POST -H 'Content-Type: application/json' \
    -d "$2" "$BASE$1"
}

# Assert on a value, not just a status code: an endpoint that answers 200
# with the wrong answer is the failure a status-only sweep sleeps through.
claim() { # claim <name> <expected> <actual>
  if [ "$2" = "$3" ]; then
    printf 'PASS  %-38s %s\n' "$1" "$2"
    pass=$((pass + 1))
  else
    printf 'FAIL  %-38s got %s (expected %s)\n' "$1" "${3:-<none>}" "$2"
    fail=$((fail + 1))
  fi
}

skip() { printf 'SKIP  %-38s %s\n' "$1" "$2"; }

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

# --- readiness and the standing watch ----------------------------------------
# /ready is allowed to say `degraded` (a frozen watch) and still answer 200;
# only a missing dependency is a 503, and that is a genuine smoke failure.
check "readiness probe"                200 GET  /ready
claim "readiness is not unready"       "False" \
  "$(json_field /ready "b['status'] == 'unready'")"
check "watch reports on itself"        200 GET  /ops/health/watch
WATCH_STATE=$(json_field /ops/health/watch "b['state']")
case "$WATCH_STATE" in
  stale|stopped)
    printf 'FAIL  %-38s %s\n' "standing watch is beating" "$WATCH_STATE"
    fail=$((fail + 1)) ;;
  *)
    printf 'PASS  %-38s %s\n' "standing watch is beating" "${WATCH_STATE:-unknown}"
    pass=$((pass + 1)) ;;
esac

# --- the pre-flight checklist, executed --------------------------------------
check "preflight runs"                 200 GET  /ops/preflight
claim "preflight is green"             "True" "$(json_field /ops/preflight "b['ok']")"

# --- identity: register, sign in, be recognised -------------------------------
if [ "$READONLY" = "True" ]; then
  # The showcase blocks writes before routing, so registration is a 403 —
  # which is itself the thing worth asserting on a public link.
  check "auth writes blocked (readonly)" 403 POST /auth/register
  check "unauthenticated /auth/me 401s"  401 GET  /auth/me
else
  SMOKE_USER="smoke-$$"
  CREDS="{\"username\": \"$SMOKE_USER\", \"password\": \"smoke-pass-12345\"}"
  post_json /auth/register "$CREDS" >/dev/null
  TOKEN=$(post_json /auth/login "$CREDS" |
    python3 -c "import json,sys; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)
  if [ -n "${TOKEN:-}" ]; then
    printf 'PASS  %-38s %s\n' "auth register -> login" "token issued"
    pass=$((pass + 1))
  else
    printf 'FAIL  %-38s %s\n' "auth register -> login" "no token issued"
    fail=$((fail + 1))
  fi
  ME=$(curl -s --max-time 30 -H "Authorization: Bearer ${TOKEN:-none}" "$BASE/auth/me" |
    python3 -c "import json,sys; print(json.load(sys.stdin).get('username',''))" 2>/dev/null)
  claim "signed-in identity is echoed"  "$SMOKE_USER" "$ME"
fi

# --- execution stays simulated unless a webhook was configured ----------------
if [ "$READONLY" = "True" ]; then
  skip "execute leaves no dispatch key" "writes are disabled on this instance"
else
  ACTION=$(json_field /actions "(b['actions'] or [{}])[0].get('id','')")
  if [ -n "${ACTION:-}" ]; then
    curl -s --max-time 30 -X POST -H 'Content-Type: application/json' \
      -d '{"rationale": "smoke sweep"}' "$BASE/actions/$ACTION/approve" >/dev/null
    EXECUTED=$(curl -s --max-time 30 -X POST "$BASE/actions/$ACTION/execute")
    claim "execute is simulated"        "SIMULATION" \
      "$(printf '%s' "$EXECUTED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('detail',{}).get('execution',{}).get('mode',''))" 2>/dev/null)"
    # No webhook configured -> no delivery record. A dispatch key here would
    # mean the demo box quietly called out to somebody.
    claim "no dispatch without a webhook" "False" \
      "$(printf '%s' "$EXECUTED" | python3 -c "import json,sys; print('dispatch' in json.load(sys.stdin).get('detail',{}).get('execution',{}))" 2>/dev/null)"
  else
    skip "execute leaves no dispatch key" "no proposal in the inbox to execute"
  fi
fi

# --- detector metrics ---------------------------------------------------------
check "detector metrics"               200 GET  /metrics/detection
check "detector backtest"              200 GET  /metrics/backtest
METRICS_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 "$BASE/metrics")
if [ "$METRICS_CODE" = "200" ]; then
  printf 'PASS  %-38s GET /metrics\n' "prometheus exposition"
  pass=$((pass + 1))
else
  skip "prometheus exposition" "/metrics not shipped on this build ($METRICS_CODE)"
fi

echo "--------------------------------------------------------------"
echo "provider: $(json_field /health "b['provider']") · env: $(json_field /health "b['env']") · version: $(json_field /health "b['version']")"
echo "watch: ${WATCH_STATE:-unknown} · preflight ok: $(json_field /ops/preflight "b['ok']")"
echo "result: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
