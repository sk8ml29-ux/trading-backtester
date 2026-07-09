#!/usr/bin/env python3
"""Optimize triple MTF: symbol list + min_align per timeframe."""

from __future__ import annotations

import itertools
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
CANDIDATES = [
    "QQQ", "SPY", "BTC-USD", "ETH-USD", "GLD", "AAPL", "SOL-USD",
    "NVDA", "MSFT", "TSLA", "EURUSD=X", "GBPUSD=X", "SLV", "USO",
]
CAPITAL = 20_000.0


def combined_score(symbols: list[str], align_map: dict[str, int | None]) -> dict:
    pairs = [(s, STRATEGY) for s in symbols]
    costs = CostConfig()
    total_end = 0.0
    bots = {}
    for tf in TIMEFRAMES:
        ma = align_map.get(tf)
        overrides = {"mtf_min_align": ma} if ma is not None else {}
        r = simulate_account(pairs, CAPITAL, 0.0075, 6, costs, entry_tf=tf, strategy_overrides=overrides)
        m = account_metrics(r)
        if "total_return_pct" not in m:
            m["total_return_pct"] = 0.0
        bots[tf] = m
        total_end += m["final_equity"]
    ret = (total_end / (CAPITAL * 3) - 1) * 100
    trades = sum(bots[tf].get("total_trades", 0) for tf in TIMEFRAMES)
    return {
        "symbols": symbols,
        "align": align_map,
        "combined_return_pct": round(ret, 2),
        "combined_end": round(total_end, 2),
        "total_trades": trades,
        "bots": bots,
    }


def main():
    print("Scanning symbol profitability per TF...")
    sym_scores: dict[str, float] = {}
    for sym in CANDIDATES:
        score = 0.0
        for tf in TIMEFRAMES:
            try:
                entry, regime, cfg = load_entry_regime(sym, tf)
                cfg.risk_per_trade = 0.0075
                df = apply_regime_to_entry(entry, regime, cfg)
                m = compute_metrics(BacktestEngine(cfg).run(df, STRATEGIES[STRATEGY](cfg)))
                score += float(m.get("total_return_pct", 0))
            except Exception:
                pass
        sym_scores[sym] = score

    ranked = sorted(sym_scores.items(), key=lambda x: x[1], reverse=True)
    print("Rank:", [(s, round(v, 1)) for s, v in ranked[:10]])

    top = [s for s, v in ranked if v > 0][:8]
    if len(top) < 3:
        top = [s for s, _ in ranked[:5]]

    align_options = [
        {"15m": None, "30m": None, "1h": None},
        {"15m": 2, "30m": None, "1h": None},
        {"15m": 2, "30m": 2, "1h": None},
    ]

    best = None
    results = []
    for n in range(3, min(7, len(top) + 1)):
        for combo in itertools.combinations(top, n):
            for align in align_options:
                r = combined_score(list(combo), align)
                results.append(r)
                if best is None or r["combined_return_pct"] > best["combined_return_pct"]:
                    best = r

    results.sort(key=lambda x: x["combined_return_pct"], reverse=True)
    print(f"\n=== BASTA TRIPLE MTF (3x20k) ===")
    if best:
        print(f"Symboler: {', '.join(best['symbols'])}")
        print(f"Align: {best['align']}")
        print(f"Totalt: {best['combined_end']:,.0f} SEK ({best['combined_return_pct']:+.1f}%)")
        print(f"Trades: {best['total_trades']}")
        for tf in TIMEFRAMES:
            b = best["bots"][tf]
            print(
                f"  {tf}: {b.get('final_equity', 20000):,.0f} SEK  "
                f"{b.get('total_return_pct', 0):+.1f}%  {b.get('total_trades', 0)} trades"
            )

    out = {"best": best, "top10": results[:10]}
    path = ROOT / "triple_mtf_optimized.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nSparat: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
