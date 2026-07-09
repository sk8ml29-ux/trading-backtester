#!/usr/bin/env python3
"""Forex paper-forward report — 20k SEK per bot."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.optimized_loader import forex_oos_portfolio_entries, live_paths

ROOT = Path(__file__).resolve().parent.parent
CAPITAL = 20_000.0


def main():
    print(f"=== FOREX PAPER RAPPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")
    total_start = 0.0
    total_equity = 0.0

    for tf in ("30m", "1h"):
        entries = forex_oos_portfolio_entries(tf)
        if not entries:
            continue
        bot_pnl = 0.0
        print(f"Bot {tf} ({len(entries)} par, {CAPITAL:,.0f} SEK per par):")
        for e in entries:
            symbol = e["symbol"]
            strategy = e["strategy"]
            state_path, _ = live_paths(symbol, strategy, tf)
            sp = ROOT / state_path
            equity = CAPITAL
            trades = 0
            if sp.exists():
                raw = json.loads(sp.read_text(encoding="utf-8"))
                equity = float(raw.get("equity", CAPITAL))
                trades = int(raw.get("trade_count", 0))
            pnl = equity - CAPITAL
            bot_pnl += pnl
            ret = pnl / CAPITAL * 100
            print(
                f"  {symbol:10} {strategy:22}  PnL {pnl:>+8,.0f} SEK  "
                f"({ret:+.1f}%)  {trades} trades"
            )
        bot_eq = CAPITAL * len(entries) + bot_pnl
        bot_start = CAPITAL * len(entries)
        print(f"  Bot totalt: {bot_eq:,.0f} SEK ({(bot_eq/bot_start-1)*100:+.1f}%)\n")
        total_start += bot_start
        total_equity += bot_eq

    if total_start:
        print(f"Kombinerat: {total_equity:,.0f} / {total_start:,.0f} SEK  ({(total_equity/total_start-1)*100:+.1f}%)")


if __name__ == "__main__":
    main()
