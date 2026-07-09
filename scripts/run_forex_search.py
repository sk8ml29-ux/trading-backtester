#!/usr/bin/env python3
"""Run systematic forex OOS search on cached Dukascopy data (15m resampled to 30m/1h)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.forex_search import (
    FOREX_SEARCH_STRATEGIES,
    FOREX_TIMEFRAMES,
    PARAM_GRIDS,
    run_forex_search,
    save_winners,
)
from research.pipeline import FOREX

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    n_strats = sum(len(PARAM_GRIDS.get(s, [{}])) for s in FOREX_SEARCH_STRATEGIES)
    total = len(FOREX) * len(FOREX_TIMEFRAMES) * n_strats
    print("=== FOREX SYSTEMATIC OOS SEARCH ===")
    print(f"Pairs: {FOREX}")
    print(f"Timeframes: {FOREX_TIMEFRAMES} (resampled from 15m cache)")
    print(f"Strategies: {len(FOREX_SEARCH_STRATEGIES)} (incl. harmonic, RSI, BB, short breakout)")
    print(f"Param combos: {n_strats}  |  Total runs: ~{total}")
    print(f"Data: cached Dukascopy 15m -> resample  |  Start: 2023-01-01")
    print(f"Priority: OOS pass + test_trades >= 20\n")

    out = run_forex_search(start="2023-01-01")
    results_path, port_path = save_winners(out)

    print(f"\nRuns: {out['total_runs']}  |  OOS passed: {out['oos_passed']}  |  HF (20+ trades): {out.get('hf_oos_passed', 0)}")

    if out.get("top_10_hf"):
        print(f"\n=== HF OOS WINNERS (20+ trades, top 10) ===")
        for r in out["top_10_hf"][:10]:
            print(
                f"  {r['symbol']:10} {r['timeframe']:4} {r['strategy']:22} "
                f"test {r['test_return_pct']:+6.2f}%  {r['test_trades']:3} trades  "
                f"PF {r['test_profit_factor']}  params={r.get('params', {})}"
            )
    elif out.get("top_10"):
        print("\n=== OOS WINNERS (top 10) ===")
        for r in out["top_10"][:10]:
            print(
                f"  {r['symbol']:10} {r['timeframe']:4} {r['strategy']:22} "
                f"test {r['test_return_pct']:+6.2f}%  {r['test_trades']:3} trades  "
                f"PF {r['test_profit_factor']}  params={r.get('params', {})}"
            )
    else:
        print("\nNo OOS passes yet.")

    if out.get("near_misses"):
        print("\n=== NEAR MISSES (top 10) ===")
        for r in out["near_misses"][:10]:
            print(
                f"  {r['symbol']:10} {r['timeframe']:4} {r['strategy']:22} "
                f"test {r['test_return_pct']:+6.2f}%  {r['test_trades']:3} trades  "
                f"PF {r['test_profit_factor']}"
            )

    print(f"\nSaved: {results_path}")
    print(f"Portfolio: {port_path}  ({len(out.get('best_per_symbol_tf', []))} pairs)")
    print("\nDone.")
    return 0 if out["oos_passed"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
