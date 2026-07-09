#!/usr/bin/env python3
"""Download and cache market data for backtesting and live bot."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.data_loader import fetch_ohlcv

DEFAULT_SYMBOLS = ["GC=F", "BTC-USD", "SPY"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Download OHLCV to data/cache/")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        help="Yahoo symbols (default: GC=F BTC-USD SPY)",
    )
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--refresh", action="store_true", help="Force re-download")
    args = parser.parse_args()

    for symbol in args.symbols:
        df = fetch_ohlcv(
            symbol,
            timeframe=args.timeframe,
            start=args.start,
            refresh=args.refresh,
        )
        print(f"  {symbol}: {len(df)} bars ({df.index[0].date()} -> {df.index[-1].date()})")

    print("\nDone. Run backtests with: py run_backtest.py --symbol GC=F")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
