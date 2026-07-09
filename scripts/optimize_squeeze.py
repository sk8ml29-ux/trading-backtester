#!/usr/bin/env python3
"""Quick grid for squeeze_breakout on top universe symbols."""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics
from backtest.mtf import apply_regime_to_entry
from config import BacktestConfig
from scripts.optimize_30m import fitness, load_full
from strategies import STRATEGIES

SYMBOLS = ["^NDX", "QQQ", "^GSPC", "CL=F", "BTC-USD", "USDCAD=X", "AAPL", "SOL-USD"]


def main():
    results = {}
    for symbol in SYMBOLS:
        entry_df, regime_df = load_full(symbol)
        best_score, best_params, best_m = -1e18, {}, {}
        for rr, bb, pct, lb, swing in itertools.product(
            [1.5, 2.0, 2.5, 3.0],
            [15, 20, 24],
            [0.15, 0.20, 0.25, 0.30],
            [80, 100, 120],
            [4, 6, 8],
        ):
            p = dict(
                reward_risk=rr,
                squeeze_bb_period=bb,
                squeeze_width_pct_max=pct,
                squeeze_width_lookback=lb,
                swing_lookback=swing,
                adx_trend_threshold=0.0,
            )
            cfg = BacktestConfig(symbol=symbol, timeframe="30m", entry_timeframe="30m", **p)
            df = apply_regime_to_entry(entry_df, regime_df, cfg)
            m = compute_metrics(BacktestEngine(cfg).run(df, STRATEGIES["squeeze_breakout"](cfg)))
            s = fitness(m)
            if s > best_score:
                best_score, best_params, best_m = s, p, m
        results[symbol] = {"params": best_params, "metrics": best_m, "fitness": best_score}
        print(
            f"{symbol}: {best_m.get('total_return_pct')}% pf={best_m.get('profit_factor')} "
            f"trades={best_m.get('total_trades')} {best_params}"
        )

    out = Path(__file__).resolve().parent.parent / "optimized_squeeze.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
