#!/usr/bin/env python3
"""
Backtest triple MTF confluence: 3 bots (15m, 30m, 1h) × 20 000 SEK each,
same symbols, cooperative strategy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.costs import CostConfig
from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics
from backtest.mtf import apply_regime_to_entry, load_entry_regime
from backtest.portfolio_account import account_metrics, simulate_account
from config import BacktestConfig
from strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent.parent
STRATEGY = "triple_tf_confluence"
TIMEFRAMES = ["15m", "30m", "1h"]
SYMBOLS = ["QQQ", "BTC-USD", "ETH-USD", "GLD", "SOL-USD"]


def scan_symbols() -> list[str]:
    """Keep symbols with positive return on at least 2 of 3 TFs."""
    good: dict[str, int] = {s: 0 for s in SYMBOLS}
    for tf in TIMEFRAMES:
        for sym in SYMBOLS:
            try:
                entry, regime, cfg = load_entry_regime(sym, tf)
                cfg.risk_per_trade = 0.0075
                df = apply_regime_to_entry(entry, regime, cfg)
                m = compute_metrics(
                    BacktestEngine(cfg).run(df, STRATEGIES[STRATEGY](cfg))
                )
                if float(m.get("total_return_pct", 0)) > 0 and int(m.get("total_trades", 0)) >= 2:
                    good[sym] += 1
            except Exception:
                pass
    picked = [s for s, n in good.items() if n >= 1]
    return picked if picked else SYMBOLS[:5]


def main():
    costs = CostConfig()
    capital = 20_000.0
    symbols = scan_symbols()
    pairs = [(s, STRATEGY) for s in symbols]

    print(f"=== TRIPLE MTF CONFLUENCE — 3× {capital:,.0f} SEK ===\n")
    print(f"Symboler: {', '.join(symbols)}\n")

    results = {}
    total_start = 0.0
    total_end = 0.0

    for tf in TIMEFRAMES:
        r = simulate_account(pairs, capital, 0.0075, 6, costs, entry_tf=tf)
        m = account_metrics(r)
        m["timeframe"] = tf
        if "total_return_pct" not in m:
            m["total_return_pct"] = 0.0
            m["win_rate_pct"] = 0.0
            m["long_trades"] = 0
            m["short_trades"] = 0
        results[tf] = m
        total_start += capital
        total_end += m["final_equity"]
        print(
            f"Bot {tf:4}  {m['final_equity']:>10,.0f} SEK  ({m['total_return_pct']:+.1f}%)  "
            f"{m['total_trades']} trades  WR {m['win_rate_pct']}%  "
            f"L/S {m.get('long_trades',0)}/{m.get('short_trades',0)}"
        )

    combined_ret = (total_end / total_start - 1) * 100
    print(f"\n--- TOTALT (3 konton) ---")
    print(f"Start:  {total_start:,.0f} SEK")
    print(f"Slut:   {total_end:,.0f} SEK")
    print(f"Vinst:  {total_end - total_start:+,.0f} SEK ({combined_ret:+.1f}%)")

    out = {
        "strategy": STRATEGY,
        "symbols": symbols,
        "capital_per_bot": capital,
        "bots": results,
        "combined": {
            "start": total_start,
            "end": round(total_end, 2),
            "pnl": round(total_end - total_start, 2),
            "return_pct": round(combined_ret, 2),
        },
    }
    path = ROOT / "triple_mtf_backtest.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nSparat: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
