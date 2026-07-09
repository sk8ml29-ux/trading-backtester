#!/bin/bash
# One-time VPS setup (Ubuntu 22.04/24.04). Run as normal user in project folder.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="trading-bot"
USER_NAME="$(whoami)"

echo "=== Trading bot VPS setup ==="
echo "App dir: $APP_DIR"

sudo apt-get update -qq
sudo apt-get install -y python3 python3-venv python3-pip

cd "$APP_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

chmod +x deploy/run_bot.sh
mkdir -p data/live data/cache

echo "Preflight check..."
python run_live.py --optimized --mixed --portfolio --once --timeframe 30m || true

# User systemd service
mkdir -p "$HOME/.config/systemd/user"
cp deploy/trading-bot.service "$HOME/.config/systemd/user/${SERVICE_NAME}.service"
sed -i "s|%h|$HOME|g" "$HOME/.config/systemd/user/${SERVICE_NAME}.service"

systemctl --user daemon-reload
systemctl --user enable "${SERVICE_NAME}.service"
systemctl --user restart "${SERVICE_NAME}.service"

sudo loginctl enable-linger "$USER_NAME" 2>/dev/null || true

CRON_LINE="0 6 * * 0 cd $APP_DIR && $APP_DIR/.venv/bin/python scripts/scan_extended.py && $APP_DIR/.venv/bin/python scripts/build_mixed_portfolio.py --timeframe 30m >> $APP_DIR/data/live/weekly_scan.log 2>&1"
( crontab -l 2>/dev/null | grep -v "build_mixed_portfolio" ; echo "$CRON_LINE" ) | crontab - || true

echo ""
echo "=== KLART ==="
echo "Status:  systemctl --user status $SERVICE_NAME"
echo "Loggar:  journalctl --user -u $SERVICE_NAME -f"
echo "Stoppa:  systemctl --user stop $SERVICE_NAME"
systemctl --user status "${SERVICE_NAME}.service" --no-pager || true
