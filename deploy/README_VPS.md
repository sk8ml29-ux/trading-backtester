# VPS — kör trading-boten 24/7

Paper-bot på en molnserver (Ubuntu). Ingen riktig broker än — simulerad handel med loggar.

## 1. Skaffa VPS

Rekommenderat (billigt, enkelt):

| Leverantör | Pris | Spec |
|------------|------|------|
| **Hetzner CX22** | ~4 EUR/mån | 2 vCPU, 4 GB RAM |
| DigitalOcean | ~6 USD/mån | Basic droplet |

Välj **Ubuntu 22.04 eller 24.04**, närmaste region (Falkenstein/Frankfurt om du är i EU).

## 2. Ladda upp projektet

**Alternativ A — från din PC (PowerShell):**

```powershell
# Packa projektet (utan cache om du vill)
cd C:\Users\Alexa\Projects
tar -czf trading-backtester.tar.gz trading-backtester --exclude=trading-backtester/.venv --exclude=trading-backtester/data/cache

# Ladda upp (byt IP och användarnamn)
scp trading-backtester.tar.gz root@DIN_VPS_IP:/home/trader/
```

På VPS:

```bash
adduser trader
usermod -aG sudo trader
su - trader
mkdir -p ~/trading-backtester
tar -xzf ~/trading-backtester.tar.gz -C ~/
mv ~/trading-backtester/* ~/trading-backtester/ 2>/dev/null || true
cd ~/trading-backtester
```

**Alternativ B — git (om du pushat till GitHub):**

```bash
git clone https://github.com/DITT_REPO/trading-backtester.git
cd trading-backtester
```

## 3. Konfigurera

Redigera `deploy/bot.env`:

```bash
nano deploy/bot.env
```

```
TIMEFRAME=30m      # eller 15m
CAPITAL=20000
RISK=0.0075
POLL=180           # 120 för 15m
```

## 4. Installera och starta (alla tre: 30m + 15m + 1h)

```bash
cd ~/trading-backtester
chmod +x deploy/vps_setup_all_tf.sh deploy/run_bot.sh
./deploy/vps_setup_all_tf.sh
```

Det startar **tre parallella bots**:

| Bot | Portfölj | Poll |
|-----|----------|------|
| `trading-bot@15m` | OOS crypto (XRP triple, ETH donchian) | var 2 min |
| `trading-bot@30m` | OOS crypto (BTC donchian) | var 3 min |
| `trading-bot@1h` | OOS crypto (XRP macd, ETH squeeze) | var 5 min |

**Paper-rapport (kör veckovis):** `python scripts/paper_report.py`

**En timeframe only:** `./deploy/vps_setup.sh` (gammalt sätt)

## 5. Kommandon på VPS

```bash
# Status alla tre
systemctl --user status trading-bot@30m trading-bot@15m trading-bot@1h

# Loggar
tail -f ~/trading-backtester/data/live/vps_bot_30m.log
tail -f ~/trading-backtester/data/live/vps_bot_15m.log
tail -f ~/trading-backtester/data/live/vps_bot_1h.log

# Stoppa / starta om alla
systemctl --user restart trading-bot@{30m,15m,1h}
```

## 6. Byt timeframe (30m ↔ 15m)

```bash
nano ~/trading-backtester/deploy/bot.env
# ändra TIMEFRAME och POLL
systemctl --user restart trading-bot
```

## 7. Thailand / resa

- VPS kör **oavsett** om din laptop är av
- Kolla loggar via SSH från mobil eller laptop
- **Paper only** — inga riktiga pengar förrän MT5/broker är kopplad

## Felsökning

**Boten startar inte**

```bash
journalctl --user -u trading-bot -n 50
```

**Ingen data**

```bash
cd ~/trading-backtester && source .venv/bin/activate
python scripts/download_data.py --symbols QQQ BTC-USD --timeframe 30m --refresh
```

**Brandvägg**

Boten behöver bara **utgående** HTTPS (Yahoo) — ingen inkommande port öppen.

## Nästa steg

1. Kör paper 1–2 veckor på VPS  
2. Jämför loggar med backtest  
3. Koppla MT5 när du är nöjd  
