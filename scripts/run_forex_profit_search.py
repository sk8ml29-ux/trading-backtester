#!/usr/bin/env python3
"""Run cost-aware forex profit search with live progress."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.forex_profit_search import (
    PROFIT_PARAM_GRIDS,
    PROFIT_STRATEGIES,
    diagnose_cost_model,
    run_profit_search,
    save_profit_results,
    update_portfolio_if_better,
    _pf_val,
)


def main() -> int:
    print("=== COST DIAGNOSIS ===", flush=True)
    diag = diagnose_cost_model()
    for k, v in diag.items():
        print(f"  {k}: {v}", flush=True)

    print("\n=== PROFIT SEARCH ===", flush=True)
    total = sum(
        len(PROFIT_PARAM_GRIDS.get(s, [{}])) * 5 * 2
        for s in PROFIT_STRATEGIES
    )
    print(f"Strategies: {len(PROFIT_STRATEGIES)}, est runs ~{total}", flush=True)

    research = run_profit_search()
    path = save_profit_results(research)

    print(f"\nDone. Runs={research['total_runs']}", flush=True)
    print(f"Quality passed (ret>0, PF>=1.2): {research['quality_passed']}", flush=True)
    print(f"Preferred (15+ trades): {research['preferred_trade_passed']}", flush=True)
    print(f"Combined OOS: {research['combined_oos_pct']}% / {research['combined_oos_trades']} trades", flush=True)
    print(f"Saved {path}", flush=True)

    if research.get("top_20"):
        print("\n=== TOP QUALITY WINNERS ===", flush=True)
        for r in research["top_20"][:15]:
            print(
                f"  {r['symbol']:10} {r['timeframe']:4} {r['strategy']:24} "
                f"{r['test_return_pct']:+.2f}%  {r['test_trades']} trades  PF {_pf_val(r['test_profit_factor']):.2f}",
                flush=True,
            )

    updated, port_path = update_portfolio_if_better(research)
    if updated:
        print(f"\nPortfolio UPDATED: {port_path}", flush=True)
    else:
        print("\nPortfolio NOT updated (did not beat success criteria)", flush=True)

    return 0 if research.get("quality_passed", 0) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
