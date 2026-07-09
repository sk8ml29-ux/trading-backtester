#!/bin/bash
# =============================================================================
# setup_server.sh – kör detta på servern för att starta handelsbottarna
# =============================================================================
set -euo pipefail
DIR="/root/trading-backtester"

echo "=== Trading Bot Setup ==="
echo ""

# ---------- 1. Kontrollera att projektet finns --
if [ ! -d "$DIR" ]; then
  echo "FEL: $DIR finns inte." >&2
  exit 1
fi
cd "$DIR"

# ---------- 2. Hitta rätt venv (hanterar både venv och .venv) --
if [ -d "$DIR/venv" ] && [ ! -d "$DIR/.venv" ]; then
  echo "Skapar symlink: venv -> .venv ..."
  ln -sfn "$DIR/venv" "$DIR/.venv"
elif [ ! -d "$DIR/.venv" ] && [ ! -d "$DIR/venv" ]; then
  echo "Ingen venv hittad – skapar ny ..."
  python3 -m venv "$DIR/.venv"
fi
PYTHON="$DIR/.venv/bin/python"
PIP="$DIR/.venv/bin/pip"

# ---------- 3. Skapa datamappar --
mkdir -p data/live data/backups

# ---------- 4. Gör shell-skript körbara --
chmod +x deploy/*.sh 2>/dev/null || true

# ---------- 5. Installera Python-paket om saknas --
if ! "$PYTHON" -c "import flask" 2>/dev/null; then
  echo "Installerar flask..."
  "$PIP" install -q flask
fi
if ! "$PYTHON" -c "import pandas" 2>/dev/null; then
  echo "Installerar pandas, numpy, ccxt, yfinance..."
  "$PIP" install -q pandas numpy ccxt yfinance requests
fi

# ---------- 6. Kopiera ALLA service-filer till systemd --
echo "Kopierar service-filer..."
cp deploy/trading-cloud.service     /etc/systemd/system/trading-cloud.service
cp deploy/trading-dashboard.service /etc/systemd/system/trading-dashboard.service
cp deploy/trading-bot@.service      /etc/systemd/system/trading-bot@.service

# ---------- 7. Ladda om systemd --
systemctl daemon-reload

# ---------- 8. Aktivera och starta tjänster --
echo ""
echo "Startar tjänster..."

systemctl enable --now trading-dashboard.service  && echo "  [OK] trading-dashboard"
systemctl enable --now trading-cloud.service       && echo "  [OK] trading-cloud"
systemctl enable --now "trading-bot@30m.service"   && echo "  [OK] trading-bot@30m"
systemctl enable --now "trading-bot@1h.service"    && echo "  [OK] trading-bot@1h"

# ---------- 9. Öppna brandvägg för dashboard --
if command -v ufw >/dev/null 2>&1; then
  ufw allow 5000/tcp >/dev/null 2>&1 && echo "  [OK] Brandvägg: port 5000 öppen"
fi

# ---------- 10. Skapa meny-skriptet --
cat > /usr/local/bin/menu << 'MENU_EOF'
#!/bin/bash
DIR="/root/trading-backtester"
PYTHON="$DIR/.venv/bin/python"

show_status() {
  echo ""
  echo "=== SYSTEMSTATUS ==="
  for s in trading-dashboard trading-cloud "trading-bot@30m" "trading-bot@1h"; do
    if systemctl is-active --quiet "$s" 2>/dev/null; then
      echo "  [ON]  $s"
    else
      echo "  [OFF] $s"
    fi
  done

  echo ""
  count=0
  for f in "$DIR/data/live/"*_state.json 2>/dev/null; do
    [ -f "$f" ] || continue
    count=$((count+1))
    symbol=$("$PYTHON" -c "import json; d=json.load(open('$f')); print(d.get('symbol','?'))" 2>/dev/null || echo "?")
    equity=$("$PYTHON" -c "import json; d=json.load(open('$f')); print(f\"{d.get('equity',0):.0f}\")" 2>/dev/null || echo "0")
    trades=$("$PYTHON" -c "import json; d=json.load(open('$f')); print(d.get('trade_count',0))" 2>/dev/null || echo "0")
    echo "  $symbol: kapital=$equity, affärer=$trades"
  done
  if [ "$count" -eq 0 ]; then
    echo "  (Inga bottar aktiva ännu – vänta ett par minuter)"
  fi
  echo ""
}

while true; do
  clear
  echo "=============================="
  echo "  TRADING SYSTEM MENY"
  echo "=============================="
  show_status
  echo "  1) Starta ALLA tjänster"
  echo "  2) Stoppa ALLA tjänster"
  echo "  3) Starta om bottar"
  echo "  4) Visa botloggar (30m)"
  echo "  5) Visa botloggar (1h)"
  echo "  6) Visa cloud-logg"
  echo "  7) Starta om dashboard"
  echo "  0) Avsluta"
  echo ""
  read -rp "Val: " val

  case "$val" in
    1) systemctl start trading-dashboard trading-cloud "trading-bot@30m" "trading-bot@1h"
       echo "Alla tjänster startade!"; sleep 2 ;;
    2) systemctl stop  trading-dashboard trading-cloud "trading-bot@30m" "trading-bot@1h"
       echo "Alla tjänster stoppade!"; sleep 2 ;;
    3) systemctl restart "trading-bot@30m" "trading-bot@1h"; echo "Bottar omstartade!"; sleep 2 ;;
    4) journalctl -u "trading-bot@30m" -n 60 --no-pager; read -rp "Enter..." _ ;;
    5) journalctl -u "trading-bot@1h"  -n 60 --no-pager; read -rp "Enter..." _ ;;
    6) journalctl -u trading-cloud     -n 60 --no-pager; read -rp "Enter..." _ ;;
    7) systemctl restart trading-dashboard; echo "Dashboard omstartad!"; sleep 2 ;;
    0) echo "Hej då!"; exit 0 ;;
    *) echo "Ogiltigt val"; sleep 1 ;;
  esac
done
MENU_EOF
chmod +x /usr/local/bin/menu

# ---------- 11. Visa slutstatus --
echo ""
echo "=============================="
echo "         KLAR!"
echo "=============================="
echo ""
IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || echo "DIN-IP")
echo "  Dashboard: http://${IP}:5000"
echo "  Kontrollera: journalctl -u trading-bot@30m -f"
echo ""
echo "  Skriv 'menu' nästa gång för att hantera systemet."
echo ""

for s in trading-dashboard trading-cloud "trading-bot@30m" "trading-bot@1h"; do
  if systemctl is-active --quiet "$s"; then
    echo "  [IGÅNG] $s"
  else
    echo "  [STOPP] $s  <-- kontrollera: journalctl -u $s -n 20"
  fi
done
