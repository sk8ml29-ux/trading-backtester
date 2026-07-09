#!/usr/bin/env python3
"""Week 1 research: forex (Dukascopy) + stocks (Polygon/Yahoo) OOS scans."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.pipeline import (
    build_portfolio_json,
    polygon_available,
    run_research_forex,
    run_research_stocks,
    save_research,
    stocks_provider_note,
)

ROOT = Path(__file__).resolve().parent.parent


def _print_top(label: str, out: dict) -> None:
    print(f"\n=== {label} ===")
    print(f"Runs: {out['total_runs']}  |  OOS passed: {out['oos_passed']}")
    if out.get("provider_note"):
        print(f"Note: {out['provider_note']}")
    for r in out.get("top_10", [])[:5]:
        print(
            f"  {r['symbol']:10} {r['timeframe']:4} {r['strategy']:22} "
            f"test {r['test_return_pct']:+6.1f}%  {r['test_trades']:3} trades  "
            f"PF {r['test_profit_factor']}  [{r['provider']}]"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Week 1 forex + stocks OOS research")
    parser.add_argument("--forex-only", action="store_true", help="Skip stocks scan")
    parser.add_argument("--stocks-only", action="store_true", help="Skip forex scan")
    parser.add_argument("--quick", action="store_true", help="Subset symbols, 1h only (fast)")
    parser.add_argument("--start", default=None, help="Override default start date (YYYY-MM-DD)")
    args = parser.parse_args()

    print("=== WEEK 1 RESEARCH (Dukascopy forex + Polygon stocks) ===\n")
    if not polygon_available():
        print(f"WARNING: {stocks_provider_note()}\n")

    forex_out = None
    stocks_out = None

    if not args.stocks_only:
        forex_symbols = ["EURUSD=X", "GBPUSD=X"] if args.quick else None
        forex_tfs = ["1h"] if args.quick else None
        forex_start = args.start or ("2025-01-01" if args.quick else "2024-01-01")
        print(f"Forex scan (Dukascopy, start {forex_start})...")
        forex_out = run_research_forex(
            symbols=forex_symbols,
            timeframes=forex_tfs,
            default_start=forex_start,
        )
        forex_path = save_research(forex_out, ROOT / "research_results_forex.json")
        _print_top("FOREX OOS", forex_out)
        print(f"Saved: {forex_path}")

        portfolio_fx = build_portfolio_json(
            forex_out,
            name="oos_paper_forex",
            description="Walk-forward OOS forex (Dukascopy). One strategy per symbol per bot.",
            source="research_results_forex.json",
        )
        fx_port_path = ROOT / "mixed_portfolio_oos_forex.json"
        fx_port_path.write_text(json.dumps(portfolio_fx, indent=2), encoding="utf-8")
        print(f"Portfolio: {fx_port_path}  ({len(portfolio_fx['pairs'])} pairs)")

    if not args.forex_only:
        if not polygon_available():
            print(f"\nERROR: {stocks_provider_note()}")
            print("Set POLYGON_API_KEY and retry.")
        else:
            stock_symbols = ["AAPL", "SPY"] if args.quick else None
            stock_tfs = ["1h"] if args.quick else None
            print("\nStocks scan (Polygon or Yahoo fallback)...")
            stocks_out = run_research_stocks(symbols=stock_symbols, timeframes=stock_tfs)
            if stocks_out.get("error"):
                print(f"\nERROR: {stocks_out['error']}")
                return 1
            stocks_path = save_research(stocks_out, ROOT / "research_results_stocks.json")
            _print_top("STOCKS OOS", stocks_out)
            print(f"Saved: {stocks_path}")

            portfolio_st = build_portfolio_json(
                stocks_out,
                name="oos_paper_stocks",
                description="Walk-forward OOS stocks/ETFs (Polygon or Yahoo).",
                source="research_results_stocks.json",
            )
            st_port_path = ROOT / "mixed_portfolio_oos_stocks.json"
            st_port_path.write_text(json.dumps(portfolio_st, indent=2), encoding="utf-8")
            print(f"Portfolio: {st_port_path}  ({len(portfolio_st['pairs'])} pairs)")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
