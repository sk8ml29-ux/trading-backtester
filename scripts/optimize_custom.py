#!/usr/bin/env python3
"""Optimize proprietary KES + ECI strategies across universe."""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics
from backtest.mtf import apply_regime_to_entry
from backtest.universe import all_symbols
from config import BacktestConfig
from scripts.optimize_30m import fitness, load_full

SYMBOLS = [
    "QQQ", "^NDX", "^GSPC", "CL=F", "BTC-USD", "USDCAD=X",
    "AAPL", "SOL-USD", "ETH-USD", "SPY", "GC=F", "NVDA",
]
CUSTOM = ["kinetic_equilibrium", "edge_compression"]


def grids():
    kes = (
        dict(
            reward_risk=rr,
            kes_entry_threshold=th,
            kes_kinetic_span=ks,
            kes_equilibrium_ema=eq,
            kes_structure_lookback=sl,
            swing_lookback=sw,
            adx_trend_threshold=0.0,
            macd_strict_trend=False,
        )
        for rr, th, ks, eq, sl, sw in itertools.product(
            [2.0, 2.5, 3.0],
            [0.05, 0.08, 0.12, 0.15, 0.18, 0.22],
            [3, 5, 8],
            [13, 21, 34],
            [6, 8, 12],
            [4, 6, 8],
        )
    )
    eci = (
        dict(
            reward_risk=rr,
            eci_entry_threshold=th,
            eci_compression_pct=cp,
            eci_pressure_window=pw,
            squeeze_bb_period=bb,
            swing_lookback=sw,
            adx_trend_threshold=0.0,
        )
        for rr, th, cp, pw, bb, sw in itertools.product(
            [1.5, 2.0, 2.5, 3.0],
            [0.20, 0.30, 0.40, 0.50],
            [0.15, 0.20, 0.25, 0.30],
            [8, 10, 12],
            [15, 20, 24],
            [4, 6, 8],
        )
    )
    return {"kinetic_equilibrium": list(kes), "edge_compression": list(eci)}


def run(symbol, strategy, params, entry_df, regime_df):
    from strategies import STRATEGIES

    cfg = BacktestConfig(symbol=symbol, timeframe="30m", entry_timeframe="30m", **params)
    df = apply_regime_to_entry(entry_df, regime_df, cfg)
    return compute_metrics(BacktestEngine(cfg).run(df, STRATEGIES[strategy](cfg)))


def main():
    grids_map = grids()
    best_overall = {"score": -1e18, "symbol": "", "strategy": "", "params": {}, "metrics": {}}
    by_strategy: dict = {}

    for strategy in CUSTOM:
        combos = grids_map[strategy]
        print(f"\n=== {strategy} ({len(combos)} combos x {len(SYMBOLS)} symbols) ===")
        strat_best = {"score": -1e18, "symbol": "", "params": {}, "metrics": {}}

        for symbol in SYMBOLS:
            try:
                entry_df, regime_df = load_full(symbol)
            except Exception as exc:
                print(f"  skip {symbol}: {exc}")
                continue

            sym_best = -1e18
            sym_params, sym_m = {}, {}
            for params in combos:
                m = run(symbol, strategy, params, entry_df, regime_df)
                s = fitness(m)
                if s > sym_best:
                    sym_best, sym_params, sym_m = s, params, m

            if sym_best > strat_best["score"]:
                strat_best = {
                    "score": sym_best,
                    "symbol": symbol,
                    "params": sym_params,
                    "metrics": sym_m,
                }
            if sym_best > best_overall["score"]:
                best_overall = {
                    "score": sym_best,
                    "symbol": symbol,
                    "strategy": strategy,
                    "params": sym_params,
                    "metrics": sym_m,
                }
            print(
                f"  {symbol}: {sym_m.get('total_return_pct', 0)}% pf={sym_m.get('profit_factor')} "
                f"trades={sym_m.get('total_trades')}"
            )

        by_strategy[strategy] = strat_best
        m = strat_best["metrics"]
        print(
            f"  BEST {strat_best['symbol']}: {m.get('total_return_pct')}% "
            f"pf={m.get('profit_factor')} fitness={strat_best['score']:.1f}"
        )

    out = Path(__file__).resolve().parent.parent / "optimized_custom.json"
    out.write_text(json.dumps({"best_overall": best_overall, "by_strategy": by_strategy}, indent=2, default=str))
    print(f"\n=== CHAMPION ===")
    bo = best_overall
    m = bo["metrics"]
    print(
        f"{bo['strategy']} on {bo['symbol']}: {m.get('total_return_pct')}% "
        f"pf={m.get('profit_factor')} trades={m.get('total_trades')}"
    )
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
