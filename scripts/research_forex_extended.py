#!/usr/bin/env python3
"""Extended forex OOS scan: 9 pairs, 14 strategies, 15m/30m/1h (Dukascopy cache)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.pipeline import (
    ALL_STRATEGIES,
    FOREX_EXTENDED,
    TIMEFRAMES,
    build_portfolio_json,
    run_research_forex_extended,
    save_research,
)

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    n = len(FOREX_EXTENDED) * len(TIMEFRAMES) * len(ALL_STRATEGIES)
    print("=== EXTENDED FOREX OOS SCAN ===")
    print(f"Pairs: {len(FOREX_EXTENDED)}  TF: {TIMEFRAMES}  Strategies: {len(ALL_STRATEGIES)}")
    print(f"Total runs: {n}  |  Start: 2023-01-01  |  Cached data = fast\n")

    out = run_research_forex_extended(default_start="2023-01-01")
    path = save_research(out, ROOT / "research_results_forex_extended.json")
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
        name="oos_paper_forex_extended",
        description="Extended forex OOS (9 pairs, 14 strategies, Dukascopy).",
        source="research_results_forex_extended.json",
    )
    port_path = ROOT / "mixed_portfolio_oos_forex_extended.json"
    port_path.write_text(json.dumps(portfolio, indent=2), encoding="utf-8")
    print(f"\nSaved: {path}")
    print(f"Portfolio: {port_path}  ({len(portfolio['pairs'])} pairs)")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
