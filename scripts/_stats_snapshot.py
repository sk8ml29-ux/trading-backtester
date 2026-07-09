#!/usr/bin/env python3
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.costs import CostConfig
from backtest.portfolio_account import account_metrics, simulate_account

ROOT = Path(__file__).resolve().parent.parent
CAP = 20_000.0
RISK = 0.0075
COSTS = CostConfig()

oos = json.loads((ROOT / "mixed_portfolio_oos.json").read_text())
by_tf: dict[str, list] = {}
for p in oos["pairs"]:
    by_tf.setdefault(p["timeframe"], []).append((p["symbol"], p["strategy"]))

print("=== CRYPTO OOS (walk-forward test) ===")
print(f"Kapital: {CAP:,.0f} SEK per TF-bot (delat) | Risk: {RISK*100:.2f}%\n")
total_s = total_e = total_tr = 0
for tf in sorted(by_tf):
    pairs = by_tf[tf]
    m = account_metrics(simulate_account(pairs, CAP, RISK, 6, COSTS, entry_tf=tf))
    total_s += CAP
    total_e += m["final_equity"]
    total_tr += m.get("total_trades", 0)
    print(
        f"Bot {tf}: {m['final_equity']:,.0f} SEK ({m.get('total_return_pct', 0):+.1f}%) "
        f"| trades={m.get('total_trades', 0)} PF={m.get('profit_factor')}"
    )
    for sym, strat in pairs:
        ref = next((b for b in oos["bots"].get(tf, []) if b["symbol"] == sym), {})
        print(f"  {sym:10} {strat:24} ref OOS {ref.get('oos_test_pct', '?')}%")
print(f"\nKombinerat: {total_e:,.0f} / {total_s:,.0f} SEK ({(total_e/total_s-1)*100:+.1f}%) | trades={total_tr}")
