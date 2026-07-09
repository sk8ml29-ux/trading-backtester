#!/usr/bin/env python3
"""Optimize Active Pulse for trade frequency + win rate >= 50% + positive expectancy."""

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
from scripts.optimize_30m import load_full
from strategies import STRATEGIES

SYMBOLS = all_symbols()
MIN_TRADES = 8


def fitness(m: dict) -> float:
    t = m["total_trades"]
    if t < MIN_TRADES:
        return -1e6 + t
    wr = m["win_rate_pct"]
    exp = m["expectancy"]
    ret = m["total_return_pct"]
    dd = abs(m["max_drawdown_pct"])
    pf = m["profit_factor"]
    if isinstance(pf, str):
        pf = 10.0

    if exp <= 0 or ret <= 0 or wr < 48:
        return ret * 5 + exp * 10 + wr * 0.5 - dd

    # reward: many trades + edge above 50% WR + 1.5 R:R expectancy
    return (
        exp * 2
        + ret * 3
        + min(t, 60) * 1.5
        + (wr - 50) * 2
        + float(pf) * 8
        - dd * 0.3
    )


def main():
    combos = [
        dict(
            pulse_rsi_period=rp,
            pulse_rsi_buy=float(rb),
            pulse_atr_sl=sl,
            pulse_reward_risk=rr,
            pulse_require_above_200=above200,
            adx_trend_threshold=adx,
        )
        for rp, rb, sl, rr, above200, adx in itertools.product(
            [10, 14, 18],
            [35, 40, 45, 50],
            [0.8, 1.0, 1.2],
            [1.2, 1.5, 1.8, 2.0],
            [True, False],
            [0, 18],
        )
    ]
    print(f"Active Pulse: {len(combos)} combos x {len(SYMBOLS)} symbols\n")

    portfolio_best = {"score": -1e18, "params": {}, "per_symbol": {}, "totals": {}}
    global_trades = 0
    global_wins = 0

    # Coarse pass: find params that maximize portfolio-wide trades + edge
    for i, params in enumerate(combos, 1):
        sym_results = []
        total_t, total_w, total_ret = 0, 0, 0.0
        for symbol in SYMBOLS:
            try:
                entry_df, regime_df = load_full(symbol)
            except Exception:
                continue
            cfg = BacktestConfig(symbol=symbol, timeframe="30m", entry_timeframe="30m", **params)
            df = apply_regime_to_entry(entry_df, regime_df, cfg)
            m = compute_metrics(BacktestEngine(cfg).run(df, STRATEGIES["active_pulse"](cfg)))
            sym_results.append(m)
            total_t += m["total_trades"]
            total_w += int(m["total_trades"] * m["win_rate_pct"] / 100)
            total_ret += m["total_return_pct"]

        if total_t < 20:
            continue
        wr = total_w / total_t * 100 if total_t else 0
        pseudo = {
            "total_trades": total_t,
            "win_rate_pct": wr,
            "total_return_pct": total_ret / max(len(sym_results), 1),
            "expectancy": total_ret / total_t if total_t else 0,
            "profit_factor": 1.5,
            "max_drawdown_pct": -5,
        }
        score = fitness(pseudo)
        if score > portfolio_best["score"]:
            portfolio_best = {
                "score": score,
                "params": params,
                "totals": {
                    "trades": total_t,
                    "wins": total_w,
                    "losses": total_t - total_w,
                    "win_rate_pct": round(wr, 2),
                    "avg_return_pct": round(total_ret / max(len(sym_results), 1), 2),
                },
            }
            print(
                f"  [{i}] BEST trades={total_t} WR={wr:.1f}% "
                f"avg_ret={portfolio_best['totals']['avg_return_pct']}% {params}"
            )

    out = Path(__file__).resolve().parent.parent / "optimized_active_pulse.json"
    out.write_text(json.dumps(portfolio_best, indent=2, default=str))
    t = portfolio_best["totals"]
    print(f"\n=== RESULT ===")
    print(f"Trades: {t.get('trades')}  Wins: {t.get('wins')}  Losses: {t.get('losses')}")
    print(f"Win rate: {t.get('win_rate_pct')}%")
    print(f"Params: {portfolio_best['params']}")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
