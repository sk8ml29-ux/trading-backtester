#!/usr/bin/env python3
"""Scan + backtest Velocity Rejection Scalp on 15m (20 000 SEK)."""

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
from backtest.universe import all_symbols, category_for
from config import BacktestConfig
from strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent.parent
STRATEGY = "velocity_rejection"
TF = "15m"


def scan_symbols() -> list[dict]:
    rows = []
    for sym in all_symbols():
        try:
            entry, regime, cfg = load_entry_regime(sym, TF)
            cfg.risk_per_trade = 0.0075
            df = apply_regime_to_entry(entry, regime, cfg)
            m = compute_metrics(BacktestEngine(cfg).run(df, STRATEGIES[STRATEGY](cfg)))
            trades = int(m.get("total_trades", 0))
            ret = float(m.get("total_return_pct", 0))
            if trades < 3:
                continue
            rows.append({
                "symbol": sym,
                "category": category_for(sym),
                "total_return_pct": ret,
                "total_trades": trades,
                "win_rate_pct": float(m.get("win_rate_pct", 0)),
                "profit_factor": m.get("profit_factor", 0),
            })
        except Exception:
            continue
    rows.sort(key=lambda x: x["total_return_pct"], reverse=True)
    return rows


def pick_portfolio(rows: list[dict], max_pairs: int = 12) -> list[str]:
    picked = []
    for r in rows:
        if r["total_return_pct"] <= 0:
            break
        pf = r.get("profit_factor", 0)
        if pf in (0, "0") or float(pf) < 1.05:
            continue
        if float(r.get("win_rate_pct", 0)) < 42:
            continue
        picked.append(r["symbol"])
        if len(picked) >= max_pairs:
            break
    if not picked:
        picked = [r["symbol"] for r in rows[:5] if r["total_return_pct"] > 0]
    return picked or ["BTC-USD", "ETH-USD", "EURUSD=X", "GC=F", "QQQ"]


def main():
    print(f"=== VRS SCALP SCAN ({TF}) ===\n")
    rows = scan_symbols()
    profitable = sum(1 for r in rows if r["total_return_pct"] > 0)
    print(f"Symboler med 3+ trades: {len(rows)}  |  Lönsamma: {profitable}\n")

    for r in rows[:8]:
        print(
            f"  {r['symbol']:12} {r['total_return_pct']:+6.1f}%  "
            f"{r['total_trades']:3} trades  WR {r['win_rate_pct']:.0f}%  {r['category']}"
        )

    symbols = pick_portfolio(rows)
    pairs = [(s, STRATEGY) for s in symbols]
    capital = 20_000.0
    costs = CostConfig()

    print(f"\n=== PORTFOLIO BACKTEST — {capital:,.0f} SEK ({TF}) ===")
    print(f"Symboler: {', '.join(symbols)}\n")

    result = simulate_account(pairs, capital, 0.0075, 6, costs, entry_tf=TF)
    m = account_metrics(result)
    if "total_return_pct" not in m:
        m["total_return_pct"] = 0.0

    print(f"Slutkapital:  {m['final_equity']:,.0f} SEK ({m['total_return_pct']:+.1f}%)")
    print(f"Trades:       {m.get('total_trades', 0)}  WR {m.get('win_rate_pct', 0)}%")
    print(f"PF:           {m.get('profit_factor', 0)}  Expectancy {m.get('expectancy', 0)}")
    print(f"L/S:          {m.get('long_trades', 0)}/{m.get('short_trades', 0)}")
    print(f"Kostnader:    {m.get('total_costs', 0):,.0f} SEK")

    out = {
        "strategy": STRATEGY,
        "timeframe": TF,
        "capital": capital,
        "symbols": symbols,
        "pairs": [{"symbol": s, "strategy": STRATEGY} for s in symbols],
        "scan_top": rows[:20],
        "metrics": m,
    }
    path = ROOT / "scalp_vrs_backtest.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    port_path = ROOT / "mixed_portfolio_scalp.json"
    port_path.write_text(
        json.dumps({"timeframe": TF, "strategy": STRATEGY, "pairs": out["pairs"]}, indent=2),
        encoding="utf-8",
    )
    print(f"\nSparat: {path}")
    print(f"Portfolio: {port_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
