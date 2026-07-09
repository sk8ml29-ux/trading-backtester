# Trading Backtester + Paper Bot

Python backtester and **paper-trading bot** for three strategies (Trading Rush / Rayner / Turtle synthesis).

| Strategy | ID | Market regime |
|----------|-----|---------------|
| MACD Pullback | `macd_pullback` | Uptrend (default) |
| Donchian Breakout | `donchian_breakout` | Trend |
| RSI Mean Reversion | `rsi_mean_reversion` | Range |

## Setup

```powershell
cd C:\Users\Alexa\Projects\trading-backtester
pip install -r requirements.txt
```

## Step 1 — Download real market data

```powershell
py scripts\download_data.py
```

Caches to `data/cache/` (Yahoo Finance chart API — works without yfinance SSL issues).

| Market | Symbol |
|--------|--------|
| Gold | `GC=F` |
| Bitcoin | `BTC-USD` |
| S&P 500 | `SPY` |

## Step 2 — Backtest

```powershell
# All strategies on gold (uses cache)
py run_backtest.py --symbol GC=F

# Bitcoin
py run_backtest.py --symbol BTC-USD --start 2017-01-01

# Stricter MACD (trend_up only)
py run_backtest.py --symbol GC=F --strategy macd_pullback --strict-trend

# Force re-download
py run_backtest.py --symbol GC=F --refresh
```

### Validated results (real data, $10k start, 1% risk, 1.5 R:R)

**Gold GC=F (2015–2026)**

| Strategy | Trades | Win rate | Profit factor | Return |
|----------|--------|----------|---------------|--------|
| MACD Pullback | 26 | 50% | 1.35 | +4.9% |
| Donchian Breakout | 12 | 83% | 7.13 | +13.6% |
| RSI Mean Reversion | 9 | 78% | 1.98 | +2.1% |

**Bitcoin BTC-USD (2017–2026)**

| Strategy | Trades | Win rate | Profit factor | Return |
|----------|--------|----------|---------------|--------|
| MACD Pullback | 16 | 50% | 1.45 | +3.7% |
| Donchian Breakout | 27 | 63% | 2.52 | +16.4% |
| RSI Mean Reversion | 10 | 80% | 2.52 | +3.2% |

MACD ~50% win rate is profitable with 1.5:1 R:R (breakeven = 40%). Trading Rush reported ~60% on 30m with manual pullback stops.

## Step 3 — Paper trading bot

```powershell
# One check (evaluate latest bar, save state)
py run_live.py --once --strategy macd_pullback --symbol GC=F

# Continuous paper trading (poll every 5 min)
py run_live.py --strategy donchian_breakout --symbol BTC-USD --poll 300

# Reset saved position/equity
py run_live.py --reset --once
```

State: `data/live_state.json`  
Log: `data/live_trades.log`

**Paper mode only** — no broker API, no real money.

## Project layout

```
backtest/       Engine, Yahoo data loader, indicators, metrics
strategies/     MACD, Donchian, RSI
live/           Paper broker + runner
scripts/        download_data.py, generate_sample_data.py
run_backtest.py Backtest CLI
run_live.py     Paper bot CLI
data/cache/     Downloaded OHLCV CSVs
```

## Defaults

- 1% risk per trade
- 1.5:1 reward/risk (trend strategies)
- 0.05% commission per side
- Regime: 9 EMA + 200 EMA + ADX ≥ 25
