#!/usr/bin/env python3
"""
OOS walk-forward test av Rsi2ReversionStrategy.
Testar aktieindex/ETF:er (1d), aktier (1d) och crypto (1h/1d).
Grid: oversold-nivå, atr_sl, exit_sma.
"""
from __future__ import annotations
import sys, json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.data_loader import fetch_ohlcv
from config import BacktestConfig
from research.walk_forward import run_walk_forward
from strategies.rsi2_reversion import Rsi2ReversionStrategy

# (symbol, timeframe, entry_start, regime_start)
UNIVERSE = [
    # Index-ETF:er 1d — Connors klassiska hemmaplan
    ("SPY",  "1d", "2012-01-01"),
    ("QQQ",  "1d", "2012-01-01"),
    ("IWM",  "1d", "2012-01-01"),
    ("DIA",  "1d", "2012-01-01"),
    ("EEM",  "1d", "2012-01-01"),
    ("XLE",  "1d", "2012-01-01"),
    ("GLD",  "1d", "2012-01-01"),
    ("SLV",  "1d", "2012-01-01"),
    ("TLT",  "1d", "2012-01-01"),
    # Aktier 1d
    ("AAPL", "1d", "2012-01-01"),
    ("MSFT", "1d", "2012-01-01"),
    ("NVDA", "1d", "2012-01-01"),
    ("AMD",  "1d", "2012-01-01"),
    ("AMZN", "1d", "2012-01-01"),
    ("GOOGL","1d", "2012-01-01"),
    ("META", "1d", "2014-01-01"),
    ("JPM",  "1d", "2012-01-01"),
    ("WMT",  "1d", "2012-01-01"),
    ("COST", "1d", "2012-01-01"),
    # Crypto 1d
    ("BTC-USD", "1d", "2018-01-01"),
    ("ETH-USD", "1d", "2018-01-01"),
    ("SOL-USD", "1d", "2021-01-01"),
    # Crypto 1h (fångar snabbare dippar)
    ("BTC-USD", "1h", "2022-01-01"),
    ("ETH-USD", "1h", "2022-01-01"),
    ("SOL-USD", "1h", "2022-01-01"),
]

# Parameter-grid
CONFIGS = [
    # (oversold, atr_sl, exit_sma, label)
    (10.0, 3.0, 5,  "RSI2<10 SL3 exit5"),
    (5.0,  3.0, 5,  "RSI2<5  SL3 exit5"),
    (15.0, 3.0, 5,  "RSI2<15 SL3 exit5"),
    (10.0, 2.5, 5,  "RSI2<10 SL2.5 exit5"),
    (10.0, 4.0, 5,  "RSI2<10 SL4 exit5"),
    (5.0,  2.5, 10, "RSI2<5  SL2.5 exit10"),
]


def run_one(sym: str, tf: str, start: str, oversold: float, atr_sl: float,
            exit_sma: int, label: str) -> dict | None:
    try:
        regime_tf = "1d" if tf == "1d" else "1d"
        cfg = BacktestConfig(
            symbol=sym, timeframe=tf, entry_timeframe=tf, regime_timeframe=regime_tf,
            reward_risk=2.0,
        )
        cfg.__dict__["rsi2_oversold"] = oversold
        cfg.__dict__["rsi2_atr_sl"]   = atr_sl
        cfg.__dict__["rsi2_exit_sma"] = exit_sma

        regime_start = "2010-01-01" if tf == "1d" else "2020-01-01"
        entry_df  = fetch_ohlcv(sym, tf,        start=start,        refresh=False)
        regime_df = fetch_ohlcv(sym, regime_tf, start=regime_start, refresh=False)

        if len(entry_df) < 300:
            return None

        strat = Rsi2ReversionStrategy(cfg)
        wf = run_walk_forward(entry_df, regime_df, cfg, strat,
                              min_test_trades=15 if tf != "1d" else 10)
        m = wf.test_metrics
        return dict(
            symbol=sym, timeframe=tf, config=label,
            oversold=oversold, atr_sl=atr_sl, exit_sma=exit_sma,
            oos_pass=wf.test_pass,
            oos_pf=round(float(m.get("profit_factor", 0) or 0), 2),
            oos_return=round(float(m.get("total_return_pct", 0) or 0), 1),
            oos_trades=int(m.get("total_trades", 0) or 0),
            oos_sharpe=round(float(m.get("sharpe", 0) or 0), 2),
            oos_winrate=round(float(m.get("win_rate_pct", 0) or 0), 1),
            oos_maxdd=round(float(m.get("max_drawdown_pct", 0) or 0), 1),
        )
    except Exception as e:
        return None


def main():
    print("=" * 74)
    print("RSI(2) MEAN REVERSION — OOS WALK-FORWARD (Connors-style)")
    print("=" * 74)

    tasks = [(sym, tf, start, *c) for (sym, tf, start) in UNIVERSE for c in CONFIGS]
    results = []

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(run_one, *t): t for t in tasks}
        done = 0
        for f in as_completed(futs):
            done += 1
            r = f.result()
            if r:
                results.append(r)
                if r["oos_pass"]:
                    print(f"  ✅ PF={r['oos_pf']:>5.2f} Sh={r['oos_sharpe']:>5.2f} "
                          f"WR={r['oos_winrate']:>4.0f}% Tr={r['oos_trades']:>3} DD={r['oos_maxdd']:>5.1f}%  "
                          f"{r['symbol']:<9}{r['timeframe']:>3}  {r['config']}")
            if done % 30 == 0:
                print(f"  ... {done}/{len(tasks)} klara")

    strong = [r for r in results if r["oos_pass"] and r["oos_pf"] >= 1.5]
    good   = [r for r in results if r["oos_pass"] and 1.1 <= r["oos_pf"] < 1.5]
    strong.sort(key=lambda x: x["oos_pf"] * max(x["oos_sharpe"], 0.1), reverse=True)
    good.sort(key=lambda x: x["oos_pf"] * max(x["oos_sharpe"], 0.1),   reverse=True)

    # Bästa per symbol
    best: dict[str, dict] = {}
    for r in results:
        if r["oos_pass"]:
            key = f"{r['symbol']}_{r['timeframe']}"
            if key not in best or (r["oos_pf"] * max(r["oos_sharpe"],0.1)) > (best[key]["oos_pf"] * max(best[key]["oos_sharpe"],0.1)):
                best[key] = r

    print(f"\n{'='*74}")
    print(f"STARKA (PF >= 1.5): {len(strong)}")
    for r in strong[:15]:
        print(f"  {r['symbol']:<9}{r['timeframe']:>3}  PF={r['oos_pf']:>5.2f}  Sh={r['oos_sharpe']:>5.2f}  "
              f"WR={r['oos_winrate']:>4.0f}%  Tr={r['oos_trades']:>3}  {r['config']}")

    print(f"\nGODKÄNDA (PF 1.1-1.5): {len(good)}")
    for r in good[:10]:
        print(f"  {r['symbol']:<9}{r['timeframe']:>3}  PF={r['oos_pf']:>5.2f}  Sh={r['oos_sharpe']:>5.2f}  "
              f"WR={r['oos_winrate']:>4.0f}%  Tr={r['oos_trades']:>3}  {r['config']}")

    print(f"\nBÄSTA PER SYMBOL ({len(best)} symboler passerade):")
    for key, r in sorted(best.items(), key=lambda x: x[1]["oos_pf"], reverse=True):
        print(f"  {key:<14}  PF={r['oos_pf']:>5.2f}  Sh={r['oos_sharpe']:>5.2f}  "
              f"Tr={r['oos_trades']:>3}  oversold={r['oversold']} atr_sl={r['atr_sl']} exit={r['exit_sma']}")

    out = ROOT / "scan_rsi2_reversion.json"
    out.write_text(json.dumps({
        "strategy": "rsi2_reversion",
        "scanned_at": str(__import__("datetime").date.today()),
        "strong": strong, "good": good, "best_per_symbol": best, "all": results,
    }, indent=2))
    print(f"\nSparat: {out}")
    print(f"Totalt: {len(strong)} starka + {len(good)} godkända av {len(results)} testade.")


if __name__ == "__main__":
    main()
