#!/usr/bin/env python3
"""Weekly paper-forward report — compare live equity vs starting capital."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.optimized_loader import live_paths, oos_portfolio_pairs

ROOT = Path(__file__).resolve().parent.parent
CAPITAL = 20_000.0


def main():
    print(f"=== PAPER FORWARD RAPPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")

    total_equity = 0.0
    total_start = 0.0
    rows = []

    for tf in ("15m", "30m", "1h"):
        pairs = oos_portfolio_pairs(tf)
        if not pairs:
            continue
        bot_start = CAPITAL
        bot_pnl = 0.0
        print(f"Bot {tf} ({len(pairs)} par, {bot_start:,.0f} SEK delat konto):")
        for symbol, strategy in pairs:
            state_path, _log_path = live_paths(symbol, strategy)
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
            rows.append({
                "timeframe": tf, "symbol": symbol, "strategy": strategy,
                "equity": equity, "pnl": pnl, "return_pct": round(ret, 2), "trades": trades,
            })
            print(
                f"  {symbol:10} {strategy:22}  PnL {pnl:>+8,.0f} SEK  "
                f"({ret:+.1f}%)  {trades} trades"
            )
        bot_equity = bot_start + bot_pnl
        bot_ret = bot_pnl / bot_start * 100
        print(f"  Bot totalt: {bot_equity:,.0f} SEK ({bot_ret:+.1f}%)\n")
        total_start += bot_start
        total_equity += bot_equity

    if total_start:
        combined = (total_equity / total_start - 1) * 100
        print(f"--- TOTALT (3×20k) ---")
        print(f"Start:  {total_start:,.0f} SEK")
        print(f"Nu:     {total_equity:,.0f} SEK")
        print(f"Avkastning: {combined:+.1f}%")
        print(f"\nFörväntan OOS backtest: ~+43% (historisk, inte garanti)")
        print("Grön flagga: inom ~20-30% av förväntad riktning efter 4-8 veckor")

    out = ROOT / "data" / "live" / "paper_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({
            "generated": datetime.now().isoformat(),
            "total_start": total_start,
            "total_equity": total_equity,
            "rows": rows,
        }, indent=2),
        encoding="utf-8",
    )
    print(f"\nSparat: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
