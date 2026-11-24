#!/usr/bin/env bash
# Re-run alert_scan every N seconds; exit with its output as soon as it finds something new.
# usage: trade_analyzer/scan_watch.sh SECONDS <alert_scan args...>
INTERVAL=$1; shift
cd "$(dirname "$0")/.."
while true; do
  OUT=$(python3 -m trade_analyzer.alert_scan "$@" 2>&1); RC=$?
  if [ $RC -eq 10 ]; then echo "ALERT"; echo "$OUT"; exit 10; fi
  echo "$OUT" | tail -1 > ~/.trade_monitor/scan.last
  sleep "$INTERVAL"
done
