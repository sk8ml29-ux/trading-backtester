# Deploy-uppdatering 2026-07-10

## Vad som ändrats (lokalt på din Mac)

| Fil | Ändring |
|-----|---------|
| `config.py` | `from __future__ import annotations` — fixar Python 3.9 TypeError |
| `backtest/providers/binance.py` | +8 nya symboler: AVAX, BNB, MATIC, LTC, DOT, FIL, ATOM, NEAR |
| `research/pipeline.py` | Utökad CRYPTO-lista med de nya symbolerna |
| `mixed_portfolio_oos.json` | **v2** — 8 par (var 5), BTC 30m ersatt, LINK+SOL 15m tillagda |

## Portfoljändring (kärnan)

**Borttaget:**
- `BTC-USD / donchian_bidirectional / 30m` — bara 3 OOS trades, statistiskt opålitlig

**Tillagt:**
- `LINK-USD / triple_tf_confluence / 15m` — OOS PF 1.45, 37 trades ✅
- `SOL-USD / triple_tf_confluence / 15m` — OOS PF 1.56, 29 trades ✅
- `BTC-USD / squeeze_bidirectional / 1h` — OOS PF 1.43, 37 trades ✅
- `XRP-USD / squeeze_bidirectional / 1h` — OOS PF 1.51, 36 trades ✅

## Deployas till server: 157.230.26.183

```bash
# 1. SSH in på servern
ssh root@157.230.26.183

# 2. Gå till projektet
cd /root/trading-backtester

# 3. Hämta ändringarna
git pull origin main

# 4. Stoppa befintliga bots
systemctl stop trading-bot@30m
systemctl stop trading-bot@15m
systemctl stop trading-bot@1h

# 5. Verifiera att Python-felet är fixat
python3 -c "from config import BacktestConfig; print('OK')"

# 6. Kontrollera att nya portföljen läses rätt
python3 -c "
from backtest.optimized_loader import oos_portfolio_pairs
print('15m:', oos_portfolio_pairs('15m'))
print('1h:', oos_portfolio_pairs('1h'))
"

# Förväntad output:
# 15m: [('XRP-USD', 'triple_tf_confluence'), ('ETH-USD', 'donchian_bidirectional'),
#        ('LINK-USD', 'triple_tf_confluence'), ('SOL-USD', 'triple_tf_confluence')]
# 1h:  [('XRP-USD', 'macd_pullback'), ('ETH-USD', 'squeeze_bidirectional'),
#        ('BTC-USD', 'squeeze_bidirectional'), ('XRP-USD', 'squeeze_bidirectional')]

# 7. Starta om bots (30m-boten läggs INTE tillbaka — ersatt av BTC-USD på 1h)
systemctl start trading-bot@15m
systemctl start trading-bot@1h

# 8. Aktivera den nya 1h-boten (om den inte redan är enabled)
systemctl enable trading-bot@1h

# 9. Kolla status
systemctl status trading-bot@15m trading-bot@1h

# 10. Följ loggar live
tail -f data/live/vps_bot_15m.log
tail -f data/live/vps_bot_1h.log
```

## Viktiga noteringar

### BTC-boten
Den gamla `trading-bot@30m` (BTC-USD donchian_bidirectional) hade **3 OOS trades**.
Det ser bra ut i backtest (PF=6.16) men är statistiskt meningslöst.
Nu kör BTC-USD istället som **squeeze_bidirectional på 1h** med 37 OOS trades.

### trading-cloud (OOS-scan var 6:e timme)
När OOS-scanen på nya symboler är klar (kör nu på din Mac → `research_results_extended.json`),
kopiera resultaten till servern:

```bash
# På din Mac
scp research_results_extended.json root@157.230.26.183:/root/trading-backtester/
```

Servern kör sen `run_cloud.py` som uppdaterar portfoljen automatiskt.

## Nästa steg (väntar på scan-resultat)

OOS-scanen på 12 symboler × 2 TF × 7 strategier körs just nu på din Mac.
När den är klar (`research_results_extended.json`):

1. Granska resultaten för AVAX, BNB, MATIC etc.
2. Om PF ≥ 1.3 och ≥ 20 OOS trades → lägg till i portfoljen
3. Ladda upp till servern

## Portfolio-sammanfattning v3 (final)

| Bot | Symbol | Strategi | OOS PF | OOS Trades | Status |
|-----|--------|----------|--------|------------|--------|
| 15m | XRP-USD | triple_tf_confluence | 1.46 | 42 | Befintlig |
| 15m | ETH-USD | donchian_bidirectional | 1.15 | 113 | Befintlig |
| 15m | LINK-USD | triple_tf_confluence | 1.45 | 37 | **NY** |
| 15m | SOL-USD | triple_tf_confluence | 1.56 | 29 | **NY** |
| 1h | NEAR-USD | macd_pullback | **1.79** | **58** | **NY** 🔥 |
| 1h | ATOM-USD | macd_pullback | **1.87** | **50** | **NY** 🔥 |
| 1h | BNB-USD | macd_pullback | **1.71** | **74** | **NY** 🔥 |
| 1h | XRP-USD | macd_pullback | 1.73 | 60 | Befintlig |
| 1h | ETH-USD | squeeze_bidirectional | 1.53 | 65 | Befintlig |
| 1h | BTC-USD | squeeze_bidirectional | 1.19 | 72 | Ersätter 30m |
