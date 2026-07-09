#!/usr/bin/env python3
"""Backtest OOS forex portfolio — 20k SEK per bot, walk-forward test slice."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.engine import BacktestEngine
from backtest.forex_loader import load_forex_entry, load_forex_regime
from backtest.metrics import compute_metrics
from backtest.mtf import apply_regime_to_entry, prepare_entry_frame
from backtest.optimized_loader import forex_oos_portfolio_path, apply_params_to_config
from config import BacktestConfig
from research.walk_forward import run_walk_forward
from strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent.parent
CAPITAL = 20_000.0


def main() -> int:
    path = forex_oos_portfolio_path()
    if not path.exists():
        print("Missing mixed_portfolio_oos_forex.json")
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    pairs = data.get("pairs", [])
    risk = float(data.get("risk_per_trade", 0.0075))

    print(f"=== FOREX PORTFOLIO BACKTEST (OOS test period) ===")
    print(f"Capital: {CAPITAL:,.0f} SEK per bot  |  Risk: {risk*100:.2f}%\n")
    print(f"{'Symbol':10} {'TF':4} {'Strategy':22} {'OOS%':>7} {'Trades':>6} {'PF':>6}")
    print("-" * 65)

    total_start = 0.0
    total_end = 0.0
    oos_pass = 0

    for p in pairs:
        symbol = p["symbol"]
        tf = p["timeframe"]
        strategy = p["strategy"]
        params = p.get("params") or {}

        entry_df = load_forex_entry(symbol, tf)
        regime_df = load_forex_regime(symbol)
        cfg = BacktestConfig(
            symbol=symbol,
            timeframe=tf,
            entry_timeframe=tf,
            regime_timeframe="1d",
            initial_capital=CAPITAL,
            risk_per_trade=risk,
        )
        apply_params_to_config(cfg, params)

        strat = STRATEGIES[strategy](cfg)
        wf = run_walk_forward(entry_df, regime_df, cfg, strat)
        tm = wf.test_metrics
        ret = float(tm.get("total_return_pct", 0))
        trades = int(tm.get("total_trades", 0))
        pf = tm.get("profit_factor", 0)
        end_eq = CAPITAL * (1 + ret / 100)

        total_start += CAPITAL
        total_end += end_eq
        if wf.test_pass:
            oos_pass += 1

        print(
            f"{symbol:10} {tf:4} {strategy:22} {ret:+6.1f}% {trades:6} {pf!s:>6}"
        )

    combined = (total_end / total_start - 1) * 100 if total_start else 0
    print("-" * 65)
    print(f"Bots: {len(pairs)}  |  OOS pass: {oos_pass}/{len(pairs)}")
    print(f"Combined: {total_end:,.0f} SEK from {total_start:,.0f} ({combined:+.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
