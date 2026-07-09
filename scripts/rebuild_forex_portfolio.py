#!/usr/bin/env python3
"""Rebuild forex portfolio from profit search quality passes."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.forex_profit_search import save_profit_results, update_portfolio_if_better

p = Path("research_results_forex_profit_search.json")
research = json.loads(p.read_text())
quality = [r for r in research["all_results"] if r.get("quality_pass")]
best: dict = {}
for r in quality:
    k = f"{r['symbol']}|{r['timeframe']}"
    if k not in best or r["score"] > best[k]["score"]:
        best[k] = r
research["best_per_symbol_tf"] = list(best.values())
research["combined_oos_pct"] = round(sum(r["test_return_pct"] for r in best.values()), 2)
research["combined_oos_trades"] = sum(r["test_trades"] for r in best.values())
save_profit_results(research)
updated, port = update_portfolio_if_better(research)
print(f"Combined: {research['combined_oos_pct']}% / {research['combined_oos_trades']} trades")
print(f"Bots: {len(best)}")
for r in sorted(best.values(), key=lambda x: -x["test_return_pct"]):
    print(f"  {r['symbol']} {r['timeframe']} {r['strategy']} {r['test_return_pct']:+.2f}% {r['test_trades']}t PF {r['test_profit_factor']}")
print(f"Portfolio updated: {port}")
