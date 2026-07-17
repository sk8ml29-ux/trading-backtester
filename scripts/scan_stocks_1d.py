#!/usr/bin/env python3
"""
OOS walk-forward scan — fler US-aktier och råvaror på 1d.
Använder de vinnande strategierna: donchian_breakout, adaptive_trend_pullback,
donchian_bidirectional, macd_pullback.
50/50 split, min 10 OOS trades.
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
from strategies import STRATEGIES

# Symbols to test
SYMBOLS = [
    # Tech/growth
    "TSLA", "META", "NFLX", "INTC", "QCOM", "AVGO", "AMAT",
    # ETFs
    "QQQ", "IWM", "XLK", "GLD", "SLV", "USO", "TLT",
    # Commodities
    "GC=F", "SI=F", "CL=F", "HG=F", "NG=F",
    # Finance
    "JPM", "V", "MA",
    # Consumer
    "COST", "WMT", "HD",
]

# Winning strategies from previous scan
WIN_STRATEGIES = ["donchian_breakout", "adaptive_trend_pullback", "donchian_bidirectional", "macd_pullback"]

TF = "1d"
START_ENTRY  = "2016-01-01"
START_REGIME = "2012-01-01"

CANDIDATES = [(sym, strat) for sym in SYMBOLS for strat in WIN_STRATEGIES]


def run_one(sym: str, strat_name: str) -> dict | None:
    try:
        cls = STRATEGIES.get(strat_name)
        if not cls:
            return None

        cfg = BacktestConfig(
            symbol=sym,
            timeframe=TF,
            entry_timeframe=TF,
            regime_timeframe=TF,
            reward_risk=2.0,
            swing_lookback=10,
        )
        entry_df  = fetch_ohlcv(sym, TF, start=START_ENTRY,  refresh=False)
        regime_df = fetch_ohlcv(sym, TF, start=START_REGIME, refresh=False)

        if len(entry_df) < 150:
            return None

        strategy = cls(cfg)
        wf = run_walk_forward(entry_df, regime_df, cfg, strategy,
                              train_ratio=0.5, min_test_trades=10)
        m = wf.test_metrics

        return dict(
            symbol=sym, strategy=strat_name, timeframe=TF,
            oos_pass=wf.test_pass,
            oos_pf=round(float(m.get("profit_factor", 0) or 0), 2),
            oos_return=round(float(m.get("total_return_pct", 0) or 0), 1),
            oos_trades=int(m.get("total_trades", 0) or 0),
            oos_sharpe=round(float(m.get("sharpe", 0) or 0), 2),
            split_date=wf.split_date,
        )
    except Exception as e:
        print(f"  FEL {sym}/{strat_name}: {e}")
        return None


def main():
    print("=" * 72)
    print(f"AKTIER/RÅVAROR 1d — {len(CANDIDATES)} kombinationer")
    print("=" * 72)

    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(run_one, sym, strat): (sym, strat) for sym, strat in CANDIDATES}
        done = 0
        for fut in as_completed(futures):
            done += 1
            r = fut.result()
            sym, strat = futures[fut]
            if r and r["oos_pass"]:
                results.append(r)
                print(f"  ✅  PF={r['oos_pf']:>5.2f}  Sh={r['oos_sharpe']:>5.2f}  "
                      f"Tr={r['oos_trades']:>3}  {sym:<8}  {strat}")
            elif r:
                results.append(r)
            if done % 20 == 0:
                print(f"  ... {done}/{len(CANDIDATES)} klara")

    strong = [r for r in results if r["oos_pass"] and r["oos_pf"] >= 1.5]
    good   = [r for r in results if r["oos_pass"] and 1.1 <= r["oos_pf"] < 1.5]
    strong.sort(key=lambda x: x["oos_pf"], reverse=True)
    good.sort(key=lambda x: x["oos_pf"],   reverse=True)

    print(f"\n{'STARKA OOS (PF >= 1.5) — 1d aktier/råvaror'}")
    print(f"  {'Symbol':<8}  {'Strategi':<25}  {'PF':>5}  {'Ret%':>6}  {'Tr':>4}  {'Sh':>5}")
    print("  " + "-" * 58)
    if strong:
        for r in strong:
            print(f"  {r['symbol']:<8}  {r['strategy']:<25}  {r['oos_pf']:>5.2f}  "
                  f"{r['oos_return']:>6.1f}  {r['oos_trades']:>4}  {r['oos_sharpe']:>5.2f}  ✅")
    else:
        print("  Inga starka.")

    if good:
        print(f"\n{'GODKÄNDA OOS (PF 1.1-1.5)'}")
        for r in good[:10]:
            print(f"  {r['symbol']:<8}  {r['strategy']:<25}  {r['oos_pf']:>5.2f}  "
                  f"{r['oos_return']:>6.1f}  {r['oos_trades']:>4}  {r['oos_sharpe']:>5.2f}")

    out = ROOT / "scan_stocks_1d.json"
    out.write_text(json.dumps({
        "scanned_at": str(__import__("datetime").date.today()),
        "timeframe": TF,
        "strong": strong,
        "good": good,
        "all": [r for r in results if r],
    }, indent=2))
    print(f"\nResultat sparade: {out}")
    print(f"Sammanfattning: {len(strong)} starka + {len(good)} godkända av {len(results)} testade.")


if __name__ == "__main__":
    main()
