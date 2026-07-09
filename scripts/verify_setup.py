#!/usr/bin/env python3
"""Quick check: Polygon key + data providers (visible output)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.providers.registry import provider_for_symbol


def main() -> int:
    print("=== SETUP CHECK ===\n")

    key = os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_KEY")
    if key:
        print(f"POLYGON_API_KEY: OK ({len(key)} tecken, borjar med {key[:4]}...)")
    else:
        print("POLYGON_API_KEY: SAKNAS")
        print("  Kor i samma PowerShell-fonster:")
        print("  $env:POLYGON_API_KEY = 'din_nyckel'")

    print()
    tests = [
        ("AAPL", "1h", "polygon/yahoo"),
        ("EURUSD=X", "1h", "dukascopy"),
        ("BTC-USD", "1h", "binance"),
    ]
    for sym, tf, label in tests:
        prov = provider_for_symbol(sym, tf)
        print(f"  {sym:12} {tf:4} -> {prov.name} ({label})")

    if not key:
        print("\nAktier anvander Yahoo tills nyckeln ar satt.")
        return 1

    print("\nTestar Polygon (AAPL, senaste 5 dagar, skriver inte cache)...")
    try:
        from datetime import datetime, timedelta

        from backtest.providers.polygon import PolygonProvider

        start = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        df = PolygonProvider().fetch("AAPL", "1h", start=start)
        print(f"  AAPL 1h: {len(df)} bars  (senaste close {df['close'].iloc[-1]:.2f})")
        print("\nAllt OK — kor vecka 1:")
        print("  python scripts/research_week1.py --quick")
    except Exception as exc:
        print(f"  FEL: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
