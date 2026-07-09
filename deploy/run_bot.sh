#!/bin/bash
# Start one timeframe portfolio bot. Usage: ./run_bot.sh 30m|15m|1h

set -euo pipefail

TF="${1:-30m}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

ENV_FILE="$DIR/deploy/bot-${TF}.env"
LOG="$DIR/data/live/vps_bot_${TF}.log"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE" >&2
  exit 1
fi

source "$DIR/.venv/bin/activate"
set -a; source "$ENV_FILE"; set +a

mkdir -p "$DIR/data/live"

MODE_FLAG="--oos"
if [ "${OOS:-1}" != "1" ]; then
  if   [ "${SCALP:-0}"  = "1" ]; then MODE_FLAG="--scalp"
  elif [ "${TRIPLE:-0}" = "1" ]; then MODE_FLAG="--triple"
  else MODE_FLAG="--mixed"
  fi
fi

exec python run_live.py \
  --optimized --portfolio "${MODE_FLAG}" \
  --timeframe "${TIMEFRAME}" \
  --capital   "${CAPITAL}" \
  --risk      "${RISK}" \
  --poll      "${POLL}" \
  2>&1 | tee -a "$LOG"
