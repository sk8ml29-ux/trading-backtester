# Trading Backtester — instruktioner

Projektmapp: `C:\Users\Alexa\Projects\trading-backtester`

**Fem strategier** — tre klassiska + två egna (cross-asset):
- **MACD Pullback** — trend, MACD-signal (Trading Rush)
- **Donchian Breakout** — trend, kanalbrott (Turtle)
- **RSI Mean Reversion** — range, oversold-bounce (Rayner)
- **Adaptive Trend Pullback** — EMA 9>21>50, köp pullback till 21 EMA *(egen)*
- **Squeeze Breakout** — Bollinger squeeze + breakout ovan band *(egen, bäst på index/ETF)*

Standard-timeframe: **30 minuter** med **daglig** regime-filter.

**Tillgångsuniversum (25 symboler):** råvaror, forex, index, ETF, aktier, krypto.
Se `backtest/universe.py` och scan-resultat i `universe_scan_30m.json`.

---

## Installation (engångs)

```powershell
cd C:\Users\Alexa\Projects\trading-backtester
pip install -r requirements.txt
py scripts\download_data.py
```

---

## Rekommenderad setup: Mixed Best-of Portfölj

**Bästa lösningen för fler trades + >50% win rate:**

Kör **rätt strategi per symbol** (inte samma överallt). Portföljen valideras mot ett **enda konto med alla kostnader**.

```powershell
py scripts\build_mixed_portfolio.py
py scripts\backtest_account.py --capital 20000
py run_live.py --optimized --mixed --portfolio --once
```

### Ett konto — 20 000 SEK (med kostnader + stop-loss + blankning)

| Mätvärde | Värde |
|----------|-------|
| Startkapital | 20 000 SEK |
| Slutkapital (~60 dagar) | **38 481 SEK** |
| Vinst | **+18 481 SEK (+92,4%)** |
| Trades | 102 (long + short) |
| Win rate | **66,7%** |
| Expectancy/trade | **+181 SEK** |
| Profit factor | **3,08** |

**Nya strategier (bull + bear):**
- `squeeze_bidirectional` — köp i bull, blanka i bear (GLD, MSFT)
- `donchian_bidirectional` — kanalbrott båda hållen (AAPL, TLT)
- `macd_bear_pullback` — blanka i nedtrend (AMZN)
- `rsi_bidirectional` — köp/sälj i sidledes marknad

Programmet läser dagligt regime (`trend_up` / `trend_down` / `range`) och väljer rätt riktning.

```powershell
py scripts\scan_extended.py
py scripts\build_mixed_portfolio.py
py scripts\backtest_account.py --capital 20000
```

### Isolerad portfölj (utan konto-simulering)

| Setup | Trades | Win rate | Expectancy/trade |
|-------|--------|----------|------------------|
| **Mixed best-of** | **132** | **65%** | **+$62** |
| Squeeze på alla | 144 | 47% | +$19 |
| RSI på alla | 311 | 37% | −$47 |

### Strategier i mixed-portföljen (20 par)

| Typ | Symboler |
|-----|----------|
| squeeze_breakout (long) | QQQ, ^NDX, BTC, CL=F, SPY, ^GSPC, SOL-USD |
| squeeze_bidirectional | GLD, MSFT |
| donchian_breakout (long) | ETH, NG=F, ^DJI |
| donchian_bidirectional | AAPL, TLT |
| macd_pullback (long) | SI=F, HG=F |
| macd_bear_pullback (short) | AMZN |
| adaptive_trend_pullback | TSLA |
| active_pulse | NVDA |
| edge_compression | USDCAD |

---

## Universum-scan — hitta bästa strategi per tillgång

Kör alla 5 strategier på alla 25 symboler (~1 min):

```powershell
py scripts\scan_universe.py
```

Resultat sparas i `universe_scan_30m.json`. Paper-boten (`--portfolio`) använder automatiskt **endast lönsamma** par från denna fil.

### Toppresultat (30m, ~60 dagar, senaste scan)

| Symbol | Kategori | Strategi | Avkastning | PF |
|--------|----------|----------|------------|-----|
| USDCAD=X | forex | Donchian | +6,9% | 3,0 |
| ^NDX | index | Donchian | +5,4% | 5,8 |
| CL=F | råvara | Squeeze | +4,9% | 5,5 |
| QQQ | ETF | Squeeze | +4,7% | 5,2 |
| ^GSPC | index | Squeeze | +4,3% | 4,3 |
| GC=F | råvara | RSI | +4,3% | 1,6 |
| BTC-USD | krypto | Squeeze | +3,5% | 2,6 |
| ETH-USD | krypto | Donchian | +3,7% | 2,7 |
| AAPL | aktie | Donchian | +2,9% | 3,7 |

**54 av 125** strategi+symbol-kombinationer var lönsamma. Forex (EUR/GBP) och vissa råvaror förlorade — strategin måste matcha tillgången.

### Optimerade egna strategier (squeeze)

```powershell
py scripts\optimize_squeeze.py
```

Sparar `optimized_squeeze.json` (t.ex. QQQ squeeze ~10% på 60d efter finjustering).

---

## Backtest — testa strategier på historisk data

### Snabbstart

```powershell
cd C:\Users\Alexa\Projects\trading-backtester

# Bästa ETF-setup (squeeze)
py run_backtest.py --symbol QQQ --entry-tf 30m --strategy squeeze_breakout

# Index
py run_backtest.py --symbol ^NDX --entry-tf 30m --strategy donchian_breakout

# Forex (bästa valutaparet)
py run_backtest.py --symbol USDCAD=X --entry-tf 30m --strategy donchian_breakout

# Guld — RSI
py run_backtest.py --symbol GC=F --entry-tf 30m --optimized --strategy rsi_mean_reversion

# Bitcoin — alla fem strategier
py run_backtest.py --symbol BTC-USD --entry-tf 30m --strategy all
```

### Vanliga flaggor

| Flagga | Betydelse |
|--------|-----------|
| `--symbol QQQ` | Tillgång — se `backtest/universe.py` för alla |
| `--entry-tf 30m` | Entry-timeframe (30m, 1h, 1d) |
| `--regime-tf 1d` | Högre TF för trend/range-filter |
| `--optimized` | Använd parametrar från optimeringen |
| `--strategy all` | Kör alla tre strategier |
| `--refresh` | Ladda ner ny data från Yahoo |
| `--json` | Skriv resultat som JSON |

### Daglig data (längre historik, färre trades)

```powershell
py run_backtest.py --symbol GC=F --entry-tf 1d --regime-tf 1d --start 2015-01-01
```

---

## Paper trading — simulerad live-bot (ingen riktig broker)

### En symbol — strategi väljs automatiskt

```powershell
cd C:\Users\Alexa\Projects\trading-backtester

# Ett enda tillfälle (kolla signal nu)
py run_live.py --symbol BTC-USD --optimized --once

# Kör kontinuerligt (kollar var 3:e minut)
py run_live.py --symbol BTC-USD --optimized
```

### Alla rekommenderade par på en gång

```powershell
py run_live.py --optimized --portfolio --once
```

### Vanliga flaggor (live)

| Flagga | Betydelse |
|--------|-----------|
| `--optimized` | Optimerade parametrar + auto-strategi per symbol |
| `--once` | Kör en gång och avsluta |
| `--portfolio` | Kör alla fyra symbol/strategi-par |
| `--poll 180` | Sekunder mellan kontroller (default 180) |
| `--reset` | Nollställ sparad position/equity |
| `--strategy macd_pullback` | Tvinga specifik strategi (överstyr auto) |
| `--json` | Skriv resultat som JSON |

### Rekommenderad strategi per symbol

Hämtas automatiskt från `universe_scan_30m.json` när du kör `--optimized`.
Kör `py scripts\scan_universe.py` för att uppdatera efter marknadsförändringar.

| Kategori | Exempel | Ofta bäst strategi |
|----------|---------|-------------------|
| Index/ETF | QQQ, ^NDX, SPY | **squeeze_breakout** |
| Forex | USDCAD=X | **donchian_breakout** |
| Krypto | BTC-USD, ETH-USD | squeeze / donchian |
| Råvaror | GC=F, CL=F | RSI / squeeze |
| Aktier | AAPL, NVDA | donchian / adaptive_trend |

---

## Viktiga filer

| Fil / mapp | Innehåll |
|------------|----------|
| `optimized_30m.json` | Globala bästa parametrar per strategi |
| `universe_scan_30m.json` | Resultat från hela universum-scan |
| `mixed_portfolio.json` | Kuraterad mixed-portfölj (konto-validerad) |
| `account_backtest_20k.json` | Senaste 20k SEK kontotest |
| `portfolio_mixed_best.json` | Aggregerad mixed utan konto-sim |
| `optimized_squeeze.json` | Optimerade squeeze-parametrar per symbol |
| `data/cache/` | Nedladdad marknadsdata (CSV) |
| `data/live/` | Paper-bot state och loggar (en fil per symbol/strategi) |
| `config.py` | Standardinställningar |
| `strategies/` | Strategikod |
| `scripts/optimize_30m.py` | Kör om optimering (tar tid) |

Exempel på live-filer:
- `data/live/btc_usd_donchian_breakout_state.json` — equity, öppen position
- `data/live/btc_usd_donchian_breakout.log` — trade-logg

---

## Begränsningar att känna till

1. **30m-data från Yahoo:** max ~60 dagar historik (gratis API).
2. **Optimering** gjordes på det fönstret — lovande men inte bevis på långsiktig edge.
3. **Paper only** — ingen koppling till riktig broker ännu.
4. **RSI kräver ADX=25** på daglig regime för att `range`-läge ska uppstå.

---

## Optimering (valfritt, tar 10–30+ min)

```powershell
py scripts\optimize_30m.py
py scripts\optimize_deep.py
py scripts\optimize_final.py
```

Resultat sparas i `optimized_30m.json` och `optimized_30m_by_symbol.json`.

---

## Felsökning

**Ingen data / SSL-fel**
```powershell
py scripts\download_data.py
```

**Tvinga omnedladdning**
```powershell
py run_backtest.py --symbol GC=F --entry-tf 30m --refresh --optimized
```

**Nollställ paper-bot**
```powershell
py run_live.py --symbol BTC-USD --optimized --reset --once
```

**"Already processed this bar"** — normalt; samma bar har redan utvärderats. Vänta på nästa 30m-bar eller kör `--reset`.

---

## Snabbreferens — copy/paste

```powershell
# Scanna hela universumet på nytt
py scripts\scan_universe.py

# Backtesta QQQ med bästa strategin
py run_backtest.py --symbol QQQ --entry-tf 30m --strategy squeeze_breakout

# Paper-bot — auto-strategi från scan
py run_live.py --symbol QQQ --optimized --once

# Alla lönsamma par
py run_live.py --optimized --portfolio --once

# Kuraterad mixed-portfölj (rekommenderad)
py run_live.py --optimized --mixed --portfolio --once

# Testa 20 000 SEK konto med kostnader
py scripts\backtest_account.py --capital 20000
```
