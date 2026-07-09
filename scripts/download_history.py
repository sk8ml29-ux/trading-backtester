#!/usr/bin/env python3
"""Download long daily history (~10 years) for portfolio symbols into data/cache/."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.data_loader import fetch_ohlcv
from backtest.optimized_loader import mixed_portfolio_pairs

ROOT = Path(__file__).resolve().parent.parent

# Core symbols if you only want a smaller set (good for VPS bandwidth)
CORE_SYMBOLS = [
    "QQQ", "SPY", "BTC-USD", "ETH-USD", "GLD", "AAPL",
    "USDCAD=X", "CL=F", "^NDX",
]


def portfolio_symbols(timeframe: str = "30m") -> list[str]:
    pairs = mixed_portfolio_pairs(timeframe)
    if pairs:
        return sorted({s for s, _ in pairs})
    path = ROOT / "mixed_portfolio.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return sorted({p["symbol"] for p in data.get("pairs", [])})
    return CORE_SYMBOLS


def main() -> int:
    parser = argparse.ArgumentParser(description="Download 10y daily OHLCV for backtest")
    parser.add_argument("--years", type=int, default=10, help="Years of history (default 10)")
    parser.add_argument("--timeframe", default="1d", help="Only 1d gives full 10y on Yahoo")
    parser.add_argument("--symbols", nargs="+", help="Override symbol list")
    parser.add_argument("--portfolio", action="store_true", help="All symbols from mixed_portfolio.json")
    parser.add_argument("--core", action="store_true", help="8 core symbols only")
    parser.add_argument("--refresh", action="store_true", help="Force re-download")
    args = parser.parse_args()

    if args.symbols:
        symbols = args.symbols
    elif args.core:
        symbols = CORE_SYMBOLS
    elif args.portfolio:
        symbols = portfolio_symbols()
    else:
        symbols = CORE_SYMBOLS

    start = (datetime.now() - timedelta(days=args.years * 365)).strftime("%Y-%m-%d")
    print(f"Downloading {args.timeframe} from {start} for {len(symbols)} symbols\n")

    manifest: dict = {
        "downloaded_at": datetime.now().isoformat(),
        "years_requested": args.years,
        "timeframe": args.timeframe,
        "start_requested": start,
        "symbols": {},
        "errors": [],
    }

    for i, symbol in enumerate(symbols, 1):
        try:
            df = fetch_ohlcv(
                symbol,
                timeframe=args.timeframe,
                start=start,
                refresh=args.refresh,
            )
            years_actual = (df.index[-1] - df.index[0]).days / 365.25
            manifest["symbols"][symbol] = {
                "bars": len(df),
                "from": str(df.index[0].date()),
                "to": str(df.index[-1].date()),
                "years": round(years_actual, 1),
            }
            print(
                f"[{i}/{len(symbols)}] {symbol:12} {len(df):4} bars  "
                f"{df.index[0].date()} -> {df.index[-1].date()}  ({years_actual:.1f} år)"
            )
        except Exception as exc:
            manifest["errors"].append({"symbol": symbol, "error": str(exc)})
            print(f"[{i}/{len(symbols)}] {symbol:12} FEL: {exc}")

    out = ROOT / "data" / "history_manifest.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    ok = len(manifest["symbols"])
    print(f"\n{ok}/{len(symbols)} OK — manifest: {out}")
    print("Kopiera data/cache/ till VPS för offline-backtest.")
    if args.timeframe != "1d":
        print("\nOBS: Endast 1d ger ~10 år. 30m/15m har max ~60 dagar på Yahoo.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
