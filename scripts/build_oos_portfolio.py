#!/usr/bin/env python3
"""Build portfolio from walk-forward OOS winners."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.costs import CostConfig
from backtest.portfolio_account import account_metrics, simulate_account

ROOT = Path(__file__).resolve().parent.parent


def main():
    src = ROOT / "research_results.json"
    if not src.exists():
        src = ROOT / "research_results_quick.json"

    data = json.loads(src.read_text(encoding="utf-8"))
    winners = data.get("top_10", [])[:6]
    pairs = [(w["symbol"], w["strategy"]) for w in winners if w.get("test_pass")]

    by_tf: dict[str, list] = {}
    for w in winners:
        if not w.get("test_pass"):
            continue
        tf = w["timeframe"]
        by_tf.setdefault(tf, []).append(w)

    print("=== OOS WINNERS PORTFOLIO ===\n")
    capital = 20_000.0
    costs = CostConfig()
    total_end = 0.0

    for tf, rows in sorted(by_tf.items()):
        tf_pairs = [(r["symbol"], r["strategy"]) for r in rows]
        r = simulate_account(tf_pairs, capital, 0.0075, 6, costs, entry_tf=tf)
        m = account_metrics(r)
        ret = m.get("total_return_pct", 0)
        total_end += m["final_equity"]
        print(f"{tf}: {m['final_equity']:,.0f} SEK ({ret:+.1f}%)  {m.get('total_trades',0)} trades")
        for row in rows:
            print(f"  {row['symbol']} / {row['strategy']}  OOS test {row['test_return_pct']:+.1f}%")

    out = {
        "name": "oos_validated_crypto",
        "source": str(src.name),
        "pairs": [{"symbol": s, "strategy": st, "timeframe": w["timeframe"]}
                  for s, st, w in zip([p[0] for p in pairs], [p[1] for p in pairs], winners[:len(pairs)])],
        "capital_per_bot": capital,
    }
    path = ROOT / "mixed_portfolio_oos.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nSparat: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
