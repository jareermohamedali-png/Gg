#!/usr/bin/env bash
# Poll an open trade every N seconds; exit (printing the alert) as soon as action is needed.
# usage: trade_analyzer/watch.sh SECONDS STATEFILE <monitor args...>
INTERVAL=$1; STATE=$2; shift 2
cd "$(dirname "$0")/.."
while true; do
  OUT=$(python3 -m trade_analyzer.monitor "$@" --state "$STATE" 2>&1); RC=$?
  if [ $RC -eq 10 ] || [ $RC -eq 11 ]; then echo "ALERT rc=$RC"; echo "$OUT"; exit $RC; fi
  echo "$(date -u +%H:%M) $OUT" | tail -1 > "$STATE.last"
  sleep "$INTERVAL"
done
