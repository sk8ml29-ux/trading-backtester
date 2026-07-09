#!/usr/bin/env python3
"""
Final 30m optimization: MACD signal modes, per-symbol params, extra symbols.
"""

from __future__ import annotations

import itertools
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.data_loader import fetch_ohlcv
from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics
from backtest.mtf import apply_regime_to_entry, prepare_entry_frame
from config import BacktestConfig
from scripts.optimize_30m import fitness, load_full
from strategies import STRATEGIES

SYMBOLS = ["GC=F", "BTC-USD", "ETH-USD", "SI=F"]
ENTRY_TF = "30m"
MIN_TRADES = 3


def run_one(strategy: str, params: dict, symbol: str, entry_df, regime_df) -> dict:
    cfg = BacktestConfig(
        symbol=symbol,
        timeframe=ENTRY_TF,
        entry_timeframe=ENTRY_TF,
        regime_timeframe="1d",
        **params,
    )
    df = apply_regime_to_entry(entry_df, regime_df, cfg)
    m = compute_metrics(BacktestEngine(cfg).run(df, STRATEGIES[strategy](cfg)))
    m["symbol"] = symbol
    m["fitness"] = fitness(m)
    return m


def search_per_symbol(strategy: str, grid, datasets: dict) -> dict:
    combos = list(grid())
    by_symbol: dict[str, dict] = {}
    print(f"\n=== PER-SYMBOL {strategy} ({len(combos)} combos x {len(datasets)} symbols) ===")

    for symbol, (entry_df, regime_df) in datasets.items():
        best_score = -1e18
        best_params = {}
        best_detail = {}
        for params in combos:
            m = run_one(strategy, params, symbol, entry_df, regime_df)
            if m["fitness"] > best_score:
                best_score = m["fitness"]
                best_params = params
                best_detail = m
        by_symbol[symbol] = {
            "best_score": best_score,
            "best_params": best_params,
            "detail": best_detail,
        }
        d = best_detail
        print(
            f"  {symbol}: score={best_score:.1f} {best_params} => "
            f"{d.get('total_trades', 0)}t {d.get('total_return_pct', 0)}% pf={d.get('profit_factor', 0)}"
        )
    return by_symbol


def macd_grid():
    for mode, rr, swing, strict, adx, bz in itertools.product(
        ["cross_below_zero", "histogram_flip", "either"],
        [1.25, 1.5, 1.75, 2.0, 2.5, 3.0],
        [4, 6, 8, 10, 12, 16],
        [False, True],
        [0, 18],
        [False, True],
    ):
        yield {
            "macd_signal_mode": mode,
            "reward_risk": rr,
            "swing_lookback": swing,
            "macd_strict_trend": strict,
            "adx_trend_threshold": float(adx),
            "macd_require_below_zero": bz,
        }


def donchian_grid():
    for entry, exit_p, rr, strict in itertools.product(
        [12, 16, 20, 24, 32, 48],
        [3, 4, 6, 8, 10],
        [1.5, 2.0, 2.5, 3.0],
        [False, True],
    ):
        if exit_p >= entry:
            continue
        yield {
            "donchian_entry": entry,
            "donchian_exit": exit_p,
            "reward_risk": rr,
            "adx_trend_threshold": 0.0,
            "donchian_strict_trend": strict,
        }


def rsi_grid():
    for period, oversold, sl, tp in itertools.product(
        [14, 16, 18, 20],
        [28, 30, 32, 35],
        [0.8, 1.0, 1.2, 1.5],
        [1.2, 1.5, 1.8, 2.0],
    ):
        yield {
            "rsi_period": period,
            "rsi_oversold": float(oversold),
            "rsi_atr_sl": sl,
            "rsi_atr_tp": tp,
            "adx_trend_threshold": 0.0,
        }


def main():
    print("Downloading / loading data...")
    datasets = {}
    for sym in SYMBOLS:
        try:
            datasets[sym] = load_full(sym)
            entry, regime = datasets[sym]
            print(f"  {sym}: {len(entry)} bars")
        except Exception as exc:
            print(f"  skip {sym}: {exc}")

    t0 = time.time()
    results = {
        "macd_pullback": search_per_symbol("macd_pullback", macd_grid, datasets),
        "donchian_breakout": search_per_symbol("donchian_breakout", donchian_grid, datasets),
        "rsi_mean_reversion": search_per_symbol("rsi_mean_reversion", rsi_grid, datasets),
    }

    out_path = Path(__file__).resolve().parent.parent / "optimized_30m_by_symbol.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {out_path} in {time.time()-t0:.0f}s")

    # Pick global compromise: params that maximize sum of per-symbol fitness
    print("\n=== GLOBAL COMPROMISE (sum of per-symbol fitness) ===")
    for strat, by_sym in results.items():
        # count param frequency among profitable symbols
        profitable = {
            s: v for s, v in by_sym.items()
            if v["detail"].get("total_return_pct", 0) > 0
            and v["detail"].get("profit_factor", 0) not in (0, "0", 0.0)
            and float(v["detail"].get("profit_factor", 0)) >= 1.0
        }
        print(f"{strat}: profitable on {list(profitable.keys()) or 'none'}")
        for s, v in by_sym.items():
            d = v["detail"]
            print(f"  {s}: {d.get('total_return_pct')}% pf={d.get('profit_factor')} trades={d.get('total_trades')}")


if __name__ == "__main__":
    main()
