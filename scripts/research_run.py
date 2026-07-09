#!/usr/bin/env python3
"""Run walk-forward research pipeline (Binance crypto + OOS validation)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.pipeline import run_research, save_research


def main():
    print("=== RESEARCH PIPELINE (walk-forward OOS) ===\n")
    out = run_research()
    path = save_research(out)
    print(f"Runs: {out['total_runs']}  |  OOS passed: {out['oos_passed']}\n")
    print("TOP 10 (out-of-sample):")
    for r in out["top_10"]:
        print(
            f"  {r['symbol']:10} {r['timeframe']:4} {r['strategy']:22} "
            f"test {r['test_return_pct']:+6.1f}%  {r['test_trades']:3} trades  "
            f"PF {r['test_profit_factor']}  [{r['provider']}]"
        )
    print(f"\nSparat: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
