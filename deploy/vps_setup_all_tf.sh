#!/bin/bash
# Install all three timeframe bots (30m + 15m + 1h) on VPS
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(whoami)"

echo "=== VPS setup: 30m + 15m + 1h ==="
cd "$APP_DIR"

sudo apt-get update -qq
sudo apt-get install -y python3 python3-venv python3-pip

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
chmod +x deploy/run_bot.sh
mkdir -p data/live data/cache

# Build OOS portfolio if missing (preferred over legacy mixed)
if [ ! -f mixed_portfolio_oos.json ]; then
  echo "Building mixed_portfolio_oos.json..."
  python scripts/build_oos_portfolio.py || true
fi

mkdir -p "$HOME/.config/systemd/user"
for tf in 30m 15m 1h; do
  sed "s|%h|$HOME|g; s|%i|$tf|g" deploy/trading-bot@.service \
    > "$HOME/.config/systemd/user/trading-bot@${tf}.service"
done

systemctl --user daemon-reload
for tf in 30m 15m 1h; do
  systemctl --user enable "trading-bot@${tf}.service"
  systemctl --user restart "trading-bot@${tf}.service"
done

sudo loginctl enable-linger "$USER_NAME" 2>/dev/null || true

echo ""
echo "=== Alla tre igång ==="
systemctl --user status trading-bot@30m --no-pager -l | head -5
systemctl --user status trading-bot@15m --no-pager -l | head -5
systemctl --user status trading-bot@1h --no-pager -l | head -5
echo ""
echo "Loggar:"
echo "  tail -f $APP_DIR/data/live/vps_bot_30m.log"
echo "  tail -f $APP_DIR/data/live/vps_bot_15m.log"
echo "  tail -f $APP_DIR/data/live/vps_bot_1h.log"
