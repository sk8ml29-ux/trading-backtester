#!/usr/bin/env python3
"""Walk-forward OOS scan for forex-specific strategies on 5 cached Dukascopy pairs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.pipeline import (
    FOREX,
    TIMEFRAMES,
    build_portfolio_json,
    run_research,
    save_research,
)

ROOT = Path(__file__).resolve().parent.parent

FOREX_STRATEGIES = [
    "forex_london_breakout",
    "forex_asian_fade",
    "forex_overlap_momentum",
]


def main() -> int:
    n = len(FOREX) * len(TIMEFRAMES) * len(FOREX_STRATEGIES)
    print("=== FOREX STRATEGY SCAN (OOS walk-forward) ===")
    print(f"Pairs: {FOREX}  TF: {TIMEFRAMES}")
    print(f"Strategies: {FOREX_STRATEGIES}")
    print(f"Total runs: {n}  |  Data: cached Dukascopy 2023+\n")

    out = run_research(
        symbols=FOREX,
        timeframes=TIMEFRAMES,
        asset_class="forex",
        default_start="2023-01-01",
        strategies=FOREX_STRATEGIES,
    )
    path = save_research(out, ROOT / "research_results_forex_strategies.json")
    print(f"\nRuns: {out['total_runs']}  |  OOS passed: {out['oos_passed']}")
    print("\nTOP 10:")
    for r in out.get("top_10", [])[:10]:
        print(
            f"  {r['symbol']:10} {r['timeframe']:4} {r['strategy']:24} "
            f"test {r['test_return_pct']:+6.1f}%  {r['test_trades']:3} trades  "
            f"PF {r['test_profit_factor']}"
        )

    portfolio = build_portfolio_json(
        out,
        name="oos_paper_forex_strategies",
        description="Forex session strategies — walk-forward OOS on 5 major pairs.",
        source="research_results_forex_strategies.json",
    )
    port_path = ROOT / "mixed_portfolio_oos_forex_strategies.json"
    port_path.write_text(json.dumps(portfolio, indent=2), encoding="utf-8")
    print(f"\nSaved: {path}")
    print(f"Portfolio: {port_path}  ({len(portfolio['pairs'])} pairs)")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
