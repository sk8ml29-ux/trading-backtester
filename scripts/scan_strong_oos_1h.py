#!/usr/bin/env python3
"""
OOS walk-forward scan — 1h timeframe på råvaror, ETF:er, aktier och index.
Tar de starka in-sample kandidaterna från universe_scan_1h.json och kör
walk-forward med 70/30 split. Sparar OOS-passerade par (PF >= 1.1, Tr >= 15).
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

# Candidates from universe_scan_1h.json (PF >= 1.5, Tr >= 20)
# Plus extra promising combinations
CANDIDATES = [
    # Råvaror (Yahoo — daglig data fungerar, 1h kan saknas)
    ("GC=F",  "donchian_breakout"),
    ("GC=F",  "squeeze_bidirectional"),
    ("GC=F",  "squeeze_breakout"),
    ("GC=F",  "adaptive_trend_pullback"),
    ("SLV",   "donchian_breakout"),
    ("SLV",   "squeeze_bidirectional"),
    ("SLV",   "adaptive_trend_pullback"),
    ("SLV",   "donchian_bidirectional"),
    ("HG=F",  "donchian_breakout"),
    ("HG=F",  "squeeze_bidirectional"),
    ("CL=F",  "donchian_breakout"),
    ("CL=F",  "squeeze_bidirectional"),
    # Aktier
    ("AMD",   "macd_pullback"),
    ("AMD",   "squeeze_bidirectional"),
    ("AMD",   "donchian_breakout"),
    ("NVDA",  "macd_pullback"),
    ("NVDA",  "squeeze_bidirectional"),
    ("AAPL",  "macd_pullback"),
    ("AAPL",  "adaptive_trend_pullback"),
    ("MSFT",  "macd_pullback"),
    ("MSFT",  "adaptive_trend_pullback"),
    ("GOOGL", "squeeze_bidirectional"),
    ("GOOGL", "macd_pullback"),
    ("SPY",   "squeeze_bidirectional"),
    ("SPY",   "adaptive_trend_pullback"),
    ("SPY",   "donchian_breakout"),
    ("AMZN",  "macd_pullback"),
    ("AMZN",  "squeeze_bidirectional"),
    # DOGE (1h, stark in-sample)
    ("DOGE-USD", "donchian_bidirectional"),
    ("DOGE-USD", "macd_pullback"),
]

TF = "1h"
START_ENTRY  = "2021-01-01"
START_REGIME = "2019-01-01"


def run_one(sym: str, strat_name: str) -> dict | None:
    try:
        cls = STRATEGIES.get(strat_name)
        if not cls:
            return None

        cfg = BacktestConfig(
            symbol=sym,
            timeframe=TF,
            entry_timeframe=TF,
            regime_timeframe="1d",
            reward_risk=2.0,
            swing_lookback=10,
        )
        entry_df  = fetch_ohlcv(sym, TF,  start=START_ENTRY,  refresh=False)
        regime_df = fetch_ohlcv(sym, "1d", start=START_REGIME, refresh=False)

        if len(entry_df) < 300:
            return None

        strategy = cls(cfg)
        wf = run_walk_forward(entry_df, regime_df, cfg, strategy)
        m  = wf.test_metrics

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
    print("OOS WALK-FORWARD — 1h: råvaror, ETF:er, aktier, crypto")
    print("=" * 72)

    results = []
    deduped = list(dict.fromkeys(CANDIDATES))
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(run_one, sym, strat): (sym, strat) for sym, strat in deduped}
        done = 0
        for fut in as_completed(futures):
            done += 1
            r = fut.result()
            sym, strat = futures[fut]
            if r:
                results.append(r)
                status = "✅" if r["oos_pass"] else "❌"
                print(f"  {done:>2}/{len(deduped)}  {status}  PF={r['oos_pf']:>5.2f}  "
                      f"Sh={r['oos_sharpe']:>5.2f}  Tr={r['oos_trades']:>3}  {sym:<12}  {strat}")
            else:
                print(f"  {done:>2}/{len(deduped)}  --  {sym:<12}  {strat}")

    strong = [r for r in results if r["oos_pass"] and r["oos_pf"] >= 1.5]
    good   = [r for r in results if r["oos_pass"] and 1.1 <= r["oos_pf"] < 1.5]
    strong.sort(key=lambda x: x["oos_pf"], reverse=True)
    good.sort(key=lambda x: x["oos_pf"],   reverse=True)

    print(f"\n{'STARKA OOS (PF >= 1.5)'}")
    print(f"  {'Symbol':<12}  {'Strategi':<25}  {'PF':>5}  {'Ret%':>6}  {'Tr':>4}  {'Sh':>5}")
    print("  " + "-" * 62)
    if strong:
        for r in strong:
            print(f"  {r['symbol']:<12}  {r['strategy']:<25}  {r['oos_pf']:>5.2f}  "
                  f"{r['oos_return']:>6.1f}  {r['oos_trades']:>4}  {r['oos_sharpe']:>5.2f}  ✅")
    else:
        print("  Inga starka OOS-kandidater.")

    if good:
        print(f"\n{'GODKÄNDA OOS (PF 1.1-1.5)'}")
        for r in good[:10]:
            print(f"  {r['symbol']:<12}  {r['strategy']:<25}  {r['oos_pf']:>5.2f}  "
                  f"{r['oos_return']:>6.1f}  {r['oos_trades']:>4}  {r['oos_sharpe']:>5.2f}")

    out = ROOT / "scan_strong_oos_1h.json"
    out.write_text(json.dumps({
        "scanned_at": str(__import__("datetime").date.today()),
        "timeframe": TF,
        "strong": strong,
        "good": good,
        "all": [r for r in results if r is not None],
    }, indent=2))
    print(f"\nResultat sparade: {out}")
    print(f"Sammanfattning: {len(strong)} starka + {len(good)} godkända av {len(results)} testade.")


if __name__ == "__main__":
    main()
