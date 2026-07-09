#!/usr/bin/env python3
"""Prefetch Binance OHLCV for all OOS paper symbols."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.data_loader import fetch_ohlcv
from backtest.optimized_loader import oos_portfolio_pairs

SYMBOLS = set()
for tf in ("15m", "30m", "1h"):
    for sym, _ in oos_portfolio_pairs(tf):
        SYMBOLS.add(sym)

TIMEFRAMES = ["15m", "30m", "1h", "1d"]


def main():
    print("=== Prefetch OOS data (Binance) ===\n")
    for sym in sorted(SYMBOLS):
        for tf in TIMEFRAMES:
            try:
                df = fetch_ohlcv(sym, tf, start="2023-01-01", refresh=True)
                print(f"OK  {sym:10} {tf:4}  {len(df):6} bars")
            except Exception as exc:
                print(f"ERR {sym:10} {tf:4}  {exc}")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
