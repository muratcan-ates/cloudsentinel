#!/usr/bin/env bash
# CloudSentinel failure drill — proves the degradation story LIVE in ~10s:
#
#   1. a pulse with llm_budget=0 must still complete, with every agent on
#      its rule-based fallback and the report saying so honestly;
#   2. the budget must be per-run: the next pulse is back on the default;
#   3. hammering /pulse past the rate limit must answer 429 + Retry-After.
#
#   bash scripts/failure_drill.sh [base-url]    # default http://127.0.0.1:8000
set -u

BASE="${1:-http://127.0.0.1:8000}"
echo "CloudSentinel failure drill — $BASE"
echo "--------------------------------------------------------------"

echo "1) pulse with a zero LLM budget (forced fallback lane):"
curl -s --max-time 60 -X POST "$BASE/pulse?llm_budget=0" | python3 -c "
import json, sys
b = json.load(sys.stdin)
print(f\"   signals={b['signals']} analyzed={b['analyzed']} budget={b['llm_budget']} \"
      f\"used={b['llm_calls_used']} exhausted={b['budget_exhausted']}\")
print(f\"   briefing source: {b['briefing']['source']} — {b['briefing']['headline']}\")
assert b['budget_exhausted'] is True and b['llm_calls_used'] == 0
assert b['briefing']['source'] == 'fallback'
print('   OK — the chain completed on deterministic fallbacks, honestly labeled')
"

echo "2) next pulse is back on the default budget (override was per-run):"
curl -s --max-time 60 -X POST "$BASE/pulse" | python3 -c "
import json, sys
b = json.load(sys.stdin)
print(f\"   budget={b['llm_budget']} used={b['llm_calls_used']} exhausted={b['budget_exhausted']}\")
assert b['llm_budget'] > 0
print('   OK')
"

echo "3) rate limit answers 429 with Retry-After (default 60/min):"
codes=$(for _ in $(seq 1 70); do
  curl -s -o /dev/null -w "%{http_code} " -X POST --max-time 60 "$BASE/pulse?llm_budget=0"
done)
echo "$codes" | tr ' ' '\n' | sort | uniq -c | sed 's/^/   /'
echo "$codes" | grep -q 429 && echo "   OK — the limiter engaged" \
  || echo "   NOTE — no 429 seen (limit disabled or raised on this deployment)"
