#!/usr/bin/env python3
"""
Optimize 30m strategies on full available Yahoo window (~60 days).
Scores combined profitability on GC=F + BTC-USD.
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
from backtest.mtf import apply_regime_to_entry, build_mtf_dataset, prepare_entry_frame
from config import BacktestConfig
from strategies import STRATEGIES

SYMBOLS = ["GC=F", "BTC-USD"]
ENTRY_TF = "30m"
MIN_TRADES = 3


def fitness(m: dict) -> float:
    t = m["total_trades"]
    if t < MIN_TRADES:
        return -1e6 + t

    pf = m["profit_factor"]
    if isinstance(pf, str):
        pf = 10.0
    ret = m["total_return_pct"]
    dd = abs(m["max_drawdown_pct"])

    if ret <= 0 or pf < 1.0:
        return ret * 10 + pf * 5 - dd

    return ret * 4 + pf * 25 + m["expectancy"] * 0.5 - dd * 0.5 + min(t, 80) * 0.2


def load_full(symbol: str):
    entry_df = fetch_ohlcv(symbol, ENTRY_TF, refresh=False)
    regime_df = fetch_ohlcv(symbol, "1d", refresh=False)
    cfg = BacktestConfig(symbol=symbol)
    return prepare_entry_frame(entry_df, cfg), regime_df


def run_combo(strategy: str, params: dict, datasets: dict) -> tuple[float, list]:
    details = []
    total = 0.0
    for symbol, (entry_df, regime_df) in datasets.items():
        cfg = BacktestConfig(
            symbol=symbol,
            timeframe=ENTRY_TF,
            entry_timeframe=ENTRY_TF,
            regime_timeframe="1d",
            **params,
        )
        from backtest.mtf import apply_regime_to_entry

        df = apply_regime_to_entry(entry_df, regime_df, cfg)
        m = compute_metrics(BacktestEngine(cfg).run(df, STRATEGIES[strategy](cfg)))
        m["symbol"] = symbol
        s = fitness(m)
        if s < -1e5:
            return -1e9, details
        total += s
        details.append(m)
    return total, details


def search(strategy: str, grid, datasets: dict) -> dict:
    combos = list(grid())
    best_score = -1e18
    best_params = {}
    best_details: list = []
    print(f"\n=== {strategy} ({len(combos)} combos) ===")
    t0 = time.time()

    for i, params in enumerate(combos, 1):
        score, details = run_combo(strategy, params, datasets)
        if score > best_score:
            best_score = score
            best_params = params
            best_details = details
            summary = " | ".join(
                f"{d['symbol']}: {d['total_trades']}t {d['total_return_pct']}% pf={d['profit_factor']}"
                for d in details
            )
            print(f"  [{i}/{len(combos)}] BEST {score:.1f} {params} => {summary}")

    print(f"  done in {time.time()-t0:.0f}s")
    return {
        "strategy": strategy,
        "best_score": best_score,
        "best_params": best_params,
        "details": best_details,
    }


def macd_grid():
    for rr, strict, swing, adx, below_zero in itertools.product(
        [1.0, 1.25, 1.5, 1.75, 2.0, 2.5],
        [False, True],
        [4, 6, 8, 10, 14, 18],
        [0, 18, 22],
        [True, False],
    ):
        yield {
            "reward_risk": rr,
            "macd_strict_trend": strict,
            "swing_lookback": swing,
            "adx_trend_threshold": float(adx),
            "macd_require_below_zero": below_zero,
        }


def donchian_grid():
    for entry, exit_p, rr, adx in itertools.product(
        [12, 20, 32, 48, 64, 96],
        [6, 10, 16, 24, 32],
        [1.0, 1.5, 2.0, 2.5],
        [0, 18, 22],
    ):
        if exit_p >= entry:
            continue
        yield {
            "donchian_entry": entry,
            "donchian_exit": exit_p,
            "reward_risk": rr,
            "adx_trend_threshold": float(adx),
        }


def rsi_grid():
    for period, oversold, sl, tp, adx in itertools.product(
        [7, 10, 14, 18],
        [28, 32, 36],
        [1.0, 1.25, 1.5, 2.0],
        [0.8, 1.0, 1.2, 1.5, 2.0],
        [0, 18, 22],
    ):
        yield {
            "rsi_period": period,
            "rsi_oversold": float(oversold),
            "rsi_atr_sl": sl,
            "rsi_atr_tp": tp,
            "adx_trend_threshold": float(adx),
        }


def main() -> int:
    print("Loading 30m datasets...")
    datasets = {}
    for sym in SYMBOLS:
        df = load_full(sym)
        datasets[sym] = df
        print(f"  {sym}: {len(df)} bars {df.index[0]} -> {df.index[-1]}")

    results = [
        search("macd_pullback", macd_grid, datasets),
        search("donchian_breakout", donchian_grid, datasets),
        search("rsi_mean_reversion", rsi_grid, datasets),
    ]

    out = Path(__file__).resolve().parent.parent / "optimized_30m.json"
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    print("\n" + "=" * 50)
    for r in results:
        print(f"\n{r['strategy']} score={r['best_score']:.1f}")
        print(f"  params: {r['best_params']}")
        for d in r["details"]:
            print(
                f"  {d['symbol']}: trades={d['total_trades']} wr={d['win_rate_pct']}% "
                f"ret={d['total_return_pct']}% pf={d['profit_factor']} dd={d['max_drawdown_pct']}%"
            )
    print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
