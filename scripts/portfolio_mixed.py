#!/usr/bin/env python3
"""Portfolio using best strategy per symbol from universe scan."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.portfolio_backtest import aggregate, run_symbol

SCAN = Path(__file__).resolve().parent.parent / "universe_scan_30m.json"
SQUEEZE = Path(__file__).resolve().parent.parent / "optimized_squeeze.json"


def main():
    scan = json.loads(SCAN.read_text())
    squeeze = json.loads(SQUEEZE.read_text()) if SQUEEZE.exists() else {}

    all_trades = []
    per_symbol = {}
    print(f"{'Symbol':12} {'Strategy':22} {'Trades':>6} {'W':>4} {'L':>4} {'WR%':>6} {'Ret%':>7}")
    print("-" * 65)

    for symbol, info in scan.get("best_per_symbol", {}).items():
        if info.get("total_return_pct", 0) <= 0:
            continue
        pf = info.get("profit_factor", 0)
        if pf in (0, "0") or float(pf) < 1.0:
            continue

        strategy = info["strategy"]
        extra = squeeze.get(symbol, {}).get("params", {}) if strategy == "squeeze_breakout" else {}
        try:
            trades, m = run_symbol(symbol, strategy, extra)
        except Exception as exc:
            print(f"{symbol:12} ERROR {exc}")
            continue
        wins = sum(1 for t in trades if t.pnl > 0)
        losses = len(trades) - wins
        per_symbol[symbol] = {**m, "wins": wins, "losses": losses, "strategy": strategy}
        all_trades.extend(trades)
        print(
            f"{symbol:12} {strategy:22} {m['total_trades']:6} {wins:4} {losses:4} "
            f"{m['win_rate_pct']:6.1f} {m['total_return_pct']:7.2f}"
        )

    agg = aggregate(all_trades)
    print("\n=== MIXED BEST-OF PORTFOLIO ===")
    print(f"Symbols:            {len(per_symbol)}")
    print(f"Total trades:       {agg['total_trades']}")
    print(f"Wins / Losses:      {agg['wins']} / {agg['losses']}")
    print(f"Win rate:           {agg['win_rate_pct']}%")
    print(f"Expectancy/trade:   ${agg['expectancy']}")
    print(f"Profit factor:      {agg['profit_factor']}")

    out = Path(__file__).resolve().parent.parent / "portfolio_mixed_best.json"
    out.write_text(json.dumps({"aggregate": agg, "per_symbol": per_symbol}, indent=2, default=str))


if __name__ == "__main__":
    main()
