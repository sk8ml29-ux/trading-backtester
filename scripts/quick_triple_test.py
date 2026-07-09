#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.costs import CostConfig
from backtest.portfolio_account import simulate_account, account_metrics

STRATEGY = "triple_tf_confluence"
CAP = 20_000
costs = CostConfig()

SETS = [
    (["QQQ", "BTC-USD", "ETH-USD", "GLD", "SOL-USD"], None),
    (["QQQ", "GLD", "BTC-USD"], None),
    (["QQQ", "GLD", "BTC-USD", "ETH-USD"], 2),
    (["GLD", "BTC-USD", "ETH-USD"], None),
    (["QQQ", "GLD"], None),
    (["BTC-USD", "ETH-USD", "GLD"], 2),
]


def run(symbols, align=None):
    pairs = [(s, STRATEGY) for s in symbols]
    end = 0
    print(f"--- {symbols} align={align} ---")
    for tf in ["15m", "30m", "1h"]:
        ov = {"mtf_min_align": align} if align else {}
        r = simulate_account(pairs, CAP, 0.0075, 6, costs, entry_tf=tf, strategy_overrides=ov)
        m = account_metrics(r)
        if "total_return_pct" not in m:
            m["total_return_pct"] = 0
        end += m["final_equity"]
        print(f"  {tf}: {m['final_equity']:,.0f} ({m['total_return_pct']:+.1f}%) {m['total_trades']} trades")
    ret = (end / (CAP * 3) - 1) * 100
    print(f"  TOTAL: {end:,.0f} SEK ({ret:+.1f}%)\n")
    return ret, end


best = None
for syms, align in SETS:
    ret, end = run(syms, align)
    if best is None or ret > best[0]:
        best = (ret, end, syms, align)

print("BEST:", best)
