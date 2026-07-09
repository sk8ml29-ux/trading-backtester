#!/usr/bin/env python3
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
r = json.loads((ROOT / "research_results_forex_winners.json").read_text())
rows = [x for x in r.get("all_results", []) if "error" not in x]

candidates = [x for x in rows if x.get("test_trades", 0) >= 15 and x.get("test_return_pct", 0) > 0]
candidates.sort(key=lambda x: (-x.get("test_trades", 0), -x.get("test_return_pct", 0)))
print("=== TOP 25 BY TRADES (positive OOS return) ===")
for x in candidates[:25]:
    print(
        f"{x['symbol']:10} {x['timeframe']:4} {x['strategy']:24} "
        f"ret={x['test_return_pct']:+.2f}% trades={x['test_trades']:3} "
        f"PF={x.get('test_profit_factor')} pass={x.get('test_pass')}"
    )

passes = [x for x in rows if x.get("test_pass")]
passes.sort(key=lambda x: -x.get("test_trades", 0))
print(f"\nAll OOS passes: {len(passes)}")
for x in passes[:20]:
    print(
        f"{x['symbol']:10} {x['timeframe']:4} {x['strategy']:24} "
        f"ret={x['test_return_pct']:+.2f}% trades={x['test_trades']:3} PF={x.get('test_profit_factor')}"
    )

# Near misses: 20+ trades, positive return
near = [
    x for x in rows
    if not x.get("test_pass") and x.get("test_trades", 0) >= 20
    and x.get("test_return_pct", 0) > 0
]
near.sort(key=lambda x: (-x.get("test_return_pct", 0), -x.get("test_trades", 0)))
print(f"\n=== NEAR MISSES (20+ trades, positive return, no pass) ===")
for x in near[:20]:
    print(
        f"{x['symbol']:10} {x['timeframe']:4} {x['strategy']:24} "
        f"ret={x['test_return_pct']:+.2f}% trades={x['test_trades']:3} PF={x.get('test_profit_factor')}"
    )

# High trade losers
losers = [x for x in rows if x.get("test_trades", 0) >= 50]
losers.sort(key=lambda x: x.get("test_return_pct", 0))
print(f"\n=== HIGH TRADE CONFIGS (50+ trades, worst return) ===")
for x in losers[:10]:
    print(
        f"{x['symbol']:10} {x['timeframe']:4} {x['strategy']:24} "
        f"ret={x['test_return_pct']:+.2f}% trades={x['test_trades']:3} PF={x.get('test_profit_factor')}"
    )

by_strat = defaultdict(list)
for x in rows:
    if x.get("test_trades", 0) > 0:
        by_strat[x["strategy"]].append(x["test_trades"])
print("\n=== AVG OOS TRADES BY STRATEGY ===")
for s, ts in sorted(by_strat.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
    print(f"{s:28} avg={sum(ts)/len(ts):.1f} max={max(ts)} n={len(ts)}")
