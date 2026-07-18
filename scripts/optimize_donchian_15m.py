#!/usr/bin/env python3
"""
Optimerar donchian_breakout på 15m för BTC, SOL, XRP.
Grid: reward_risk och swing_lookback.
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

SYMBOLS = ["BTC-USD", "SOL-USD", "XRP-USD"]
RR_LIST = [1.5, 2.0, 2.5, 3.0]
SL_LIST = [6, 8, 12, 16, 20]


def test(sym: str, rr: float, sl: int) -> dict | None:
    try:
        cfg = BacktestConfig(
            symbol=sym, timeframe="15m", entry_timeframe="15m",
            regime_timeframe="1d", reward_risk=rr, swing_lookback=sl,
        )
        entry_df  = fetch_ohlcv(sym, "15m", start="2024-01-01", refresh=False)
        regime_df = fetch_ohlcv(sym, "1d",  start="2023-01-01", refresh=False)
        if len(entry_df) < 500:
            return None
        strat = STRATEGIES["donchian_breakout"](cfg)
        wf    = run_walk_forward(entry_df, regime_df, cfg, strat)
        m     = wf.test_metrics
        pf    = float(m.get("profit_factor", 0) or 0)
        sh    = float(m.get("sharpe", 0) or 0)
        tr    = int(m.get("total_trades", 0) or 0)
        ret   = float(m.get("total_return_pct", 0) or 0)
        return dict(sym=sym, rr=rr, sl=sl, pf=round(pf,2), sh=round(sh,2),
                    tr=tr, ret=round(ret,1), passed=wf.test_pass)
    except Exception as e:
        return None


def main():
    print("=" * 60)
    print("OPTIMIZE donchian_breakout 15m — grid search")
    print("=" * 60)

    tasks = [(sym, rr, sl) for sym in SYMBOLS for rr in RR_LIST for sl in SL_LIST]
    results = []

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(test, *t): t for t in tasks}
        for f in as_completed(futs):
            r = f.result()
            if r:
                results.append(r)

    passed = [r for r in results if r["passed"]]
    passed.sort(key=lambda x: x["pf"] * max(x["sh"], 0.01), reverse=True)

    print(f"\nPasserade OOS: {len(passed)}/{len(results)}")
    print(f"  {'Sym':<10} {'RR':>4} {'SL':>4} {'PF':>6} {'Sh':>6} {'Tr':>4} {'Ret':>6}")
    print("  " + "-" * 48)
    for r in passed[:15]:
        print(f"  {r['sym']:<10} {r['rr']:>4.1f} {r['sl']:>4} "
              f"{r['pf']:>6.2f} {r['sh']:>6.2f} {r['tr']:>4} {r['ret']:>6.1f}%  ✅")

    # Bästa per symbol
    best: dict[str, dict] = {}
    for r in passed:
        if r["sym"] not in best or r["pf"] * max(r["sh"],0.01) > best[r["sym"]]["pf"] * max(best[r["sym"]]["sh"],0.01):
            best[r["sym"]] = r

    print("\nBästa per symbol:")
    for sym, r in best.items():
        print(f"  {sym}: RR={r['rr']} SL={r['sl']} → PF={r['pf']} Sh={r['sh']} Tr={r['tr']}")

    out = ROOT / "scan_donchian_15m_opt.json"
    out.write_text(json.dumps({"best_per_symbol": best, "all_passed": passed, "all": results}, indent=2))
    print(f"\nSparat: {out}")


if __name__ == "__main__":
    main()
