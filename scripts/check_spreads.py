#!/usr/bin/env python3
"""Show spread limits and tradeability for mixed portfolio symbols."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.portfolio_account import load_mixed_pairs
from backtest.spread import get_spread_monitor
from tabulate import tabulate

# Example price for stop-distance check (rough mid prices)
REF_PRICE = {
    "QQQ": 500, "^NDX": 20000, "BTC-USD": 95000, "EURUSD=X": 1.08,
    "GC=F": 2650, "GLD": 240, "AAPL": 220, "SOL-USD": 180,
}


def main():
    monitor = get_spread_monitor()
    pairs = load_mixed_pairs(strict=False)
    if not pairs:
        pairs = [("QQQ", "squeeze_breakout"), ("BTC-USD", "squeeze_breakout")]

    rows = []
    for symbol, _ in pairs:
        price = REF_PRICE.get(symbol, 100.0)
        stop_dist = price * 0.01  # assume ~1% stop
        check = monitor.check_trade(symbol, price, stop_dist)
        rows.append([
            symbol,
            monitor.mt5_symbol(symbol) or "—",
            f"{check.typical_spread_pct*100:.3f}%",
            f"{check.max_spread_pct*100:.3f}%",
            f"{check.current_spread_pct*100:.3f}%",
            check.source,
            "JA" if check.tradeable else "NEJ",
            check.reason if not check.tradeable else "",
        ])

    print("\n=== SPREAD — MIXED PORTFÖLJ ===\n")
    print(tabulate(
        rows,
        headers=["Symbol", "MT5", "Typisk", "Max", "Nuvarande", "Källa", "OK?", "Notis"],
        tablefmt="simple",
    ))
    print("\nRedigera spread_config.json för att justera gränser.")
    print("Live MT5: monitor.update_live(symbol, bid, ask, persist=True)")


if __name__ == "__main__":
    main()
