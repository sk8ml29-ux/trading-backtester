#!/usr/bin/env python3
"""Backtest mixed portfolio on a single account (default 20 000 SEK)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.costs import CostConfig
from backtest.portfolio_account import (
    account_metrics,
    load_mixed_pairs,
    simulate_account,
)
from tabulate import tabulate


def main():
    parser = argparse.ArgumentParser(description="Single-account mixed portfolio backtest")
    parser.add_argument("--capital", type=float, default=20_000.0, help="Starting capital (SEK)")
    parser.add_argument("--risk", type=float, default=0.0075, help="Risk per trade (0.0075 = 0.75%%)")
    parser.add_argument("--max-open", type=int, default=6, help="Max concurrent positions")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    pairs = load_mixed_pairs(strict=True)
    if not pairs:
        print("No mixed pairs found. Run: py scripts/build_mixed_portfolio.py")
        return 1

    costs = CostConfig()
    result = simulate_account(
        pairs,
        initial_capital=args.capital,
        risk_per_trade=args.risk,
        max_concurrent=args.max_open,
        costs=costs,
    )
    metrics = account_metrics(result)
    metrics["pairs"] = len(pairs)
    metrics["currency"] = "SEK"

    if args.json:
        print(json.dumps(metrics, indent=2))
        return 0

    print(f"\n=== MIXED PORTFOLIO — ETT KONTO {args.capital:,.0f} SEK ===\n")
    print(f"Symbol/strategi-par:  {len(pairs)}")
    print(f"Risk per trade:       {args.risk * 100:.2f}%")
    print(f"Max samtidiga trades: {args.max_open}")
    print(f"Kostnader:            courtage {costs.commission_pct*100:.3f}%/sida, "
          f"slippage {costs.slippage_pct*100:.3f}%, spread {costs.spread_pct*100:.3f}%")
    print(f"Stop-loss:            JA (varje strategi har SL + TP)\n")

    rows = [
        ["Startkapital", f"{metrics['initial_capital']:,.2f} SEK"],
        ["Slutkapital", f"{metrics['final_equity']:,.2f} SEK"],
        ["Vinst/förlust", f"{metrics['total_pnl']:+,.2f} SEK ({metrics['total_return_pct']:+.2f}%)"],
        ["Totala trades", metrics["total_trades"]],
        ["Vinster / Förluster", f"{metrics['wins']} / {metrics['losses']}"],
        ["Win rate", f"{metrics['win_rate_pct']}%"],
        ["Snitt vinst", f"{metrics['avg_win']:+,.2f} SEK"],
        ["Snitt förlust", f"{metrics['avg_loss']:+,.2f} SEK"],
        ["Expectancy/trade", f"{metrics['expectancy']:+,.2f} SEK"],
        ["Profit factor", metrics["profit_factor"]],
        ["Totala kostnader", f"{metrics['total_costs']:,.2f} SEK"],
        ["SL-utsteg / TP-utsteg", f"{metrics['stop_loss_exits']} / {metrics['take_profit_exits']}"],
        ["Long / Short trades", f"{metrics.get('long_trades', '?')} / {metrics.get('short_trades', '?')}"],
        ["Skippade (max open)", metrics["skipped_trades"]],
        ["Skippade (spread)", metrics.get("spread_skipped", 0)],
    ]
    print(tabulate(rows, headers=["Mätvärde", "Värde"], tablefmt="simple"))

    # Per symbol summary
    by_sym: dict = {}
    for t in result.trades:
        key = f"{t.symbol} ({t.strategy})"
        by_sym.setdefault(key, {"w": 0, "l": 0, "pnl": 0.0})
        if t.pnl > 0:
            by_sym[key]["w"] += 1
        else:
            by_sym[key]["l"] += 1
        by_sym[key]["pnl"] += t.pnl

    print("\n=== PER SYMBOL ===")
    sym_rows = sorted(
        [(k, v["w"], v["l"], v["pnl"]) for k, v in by_sym.items()],
        key=lambda x: -x[3],
    )
    print(tabulate(sym_rows, headers=["Par", "V", "F", "PnL SEK"], tablefmt="simple"))

    out = Path(__file__).resolve().parent.parent / "account_backtest_20k.json"
    out.write_text(json.dumps({"metrics": metrics, "pairs": pairs}, indent=2, default=str), encoding="utf-8")
    print(f"\nSparat: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
