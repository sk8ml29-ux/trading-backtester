#!/usr/bin/env bash
# Called by systemd ExecStopPost= when trading-cloud.service exits unexpectedly.
# Runs rollback via the backup manager, then logs the event.

set -euo pipefail

REPO="$HOME/trading-backtester"
LOG="$REPO/data/live/cloud.log"
PYTHON="$REPO/.venv/bin/python"

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)  [rollback_on_crash] Service stopped — attempting rollback" >> "$LOG"

if "$PYTHON" - <<'PYEOF' >> "$LOG" 2>&1
import sys
sys.path.insert(0, ".")
from state.backup_manager import rollback, get_latest_stable

target = get_latest_stable()
if target is None:
    print("No stable backup found — nothing to rollback")
    sys.exit(0)

ok = rollback(target)
if ok:
    print(f"Rollback OK from {target}")
else:
    print("Rollback FAILED")
    sys.exit(1)
PYEOF
then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)  [rollback_on_crash] Rollback complete" >> "$LOG"
else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)  [rollback_on_crash] Rollback script error (exit $?)" >> "$LOG"
fi
