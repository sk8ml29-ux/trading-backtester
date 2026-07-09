#!/usr/bin/env python3
"""Quick portfolio variant comparison."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.costs import CostConfig
from backtest.portfolio_account import account_metrics, simulate_account, load_mixed_pairs

pairs = load_mixed_pairs()
costs = CostConfig()

variants = {
    "Nuvarande (14 par)": pairs,
    "Utan USDCAD": [p for p in pairs if p[0] != "USDCAD=X"],
    "Utan USDCAD + NVDA": [p for p in pairs if p[0] not in ("USDCAD=X", "NVDA")],
}

# Add GC=F and HG=F from scan
extra = [("GC=F", "rsi_mean_reversion"), ("HG=F", "macd_pullback")]
variants["Utan USDCAD + GC/HG"] = variants["Utan USDCAD"] + extra

for label, p in variants.items():
    r = simulate_account(p, 20000, 0.0075, 6, costs)
    m = account_metrics(r)
    print(
        f"{label:30} {m['final_equity']:>10.0f} SEK  "
        f"{m['total_return_pct']:>+6.1f}%  {m['total_trades']:>3}t  "
        f"WR {m['win_rate_pct']:.0f}%  PF {m['profit_factor']}"
    )
