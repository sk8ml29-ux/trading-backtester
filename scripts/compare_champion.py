#!/usr/bin/env python3
"""Quick scan: proprietary strategies vs squeeze champion on key symbols."""

from __future__ import annotations

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

COMPARE = [
    "squeeze_breakout",
    "kinetic_equilibrium",
    "edge_compression",
    "donchian_breakout",
]
TOP_SYMBOLS = [
    "QQQ", "^NDX", "^GSPC", "CL=F", "BTC-USD", "USDCAD=X",
    "AAPL", "SOL-USD", "ETH-USD", "SPY", "GC=F", "NVDA",
]


def load_squeeze_params(symbol: str) -> dict:
    p = Path(__file__).resolve().parent.parent / "optimized_squeeze.json"
    if p.exists():
        data = json.loads(p.read_text())
        if symbol in data:
            return data[symbol].get("params", {})
    return {}


def main():
    results = []
    for symbol in TOP_SYMBOLS:
        try:
            entry_df, regime_df = load_full(symbol)
        except Exception as exc:
            print(f"skip {symbol}: {exc}")
            continue
        for strategy in COMPARE:
            cfg = BacktestConfig(symbol=symbol, timeframe="30m", entry_timeframe="30m")
            extra = load_squeeze_params(symbol) if strategy == "squeeze_breakout" else {}
            if strategy == "rsi_mean_reversion":
                cfg.adx_trend_threshold = 25.0
            for k, v in extra.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            df = apply_regime_to_entry(entry_df, regime_df, cfg)
            m = compute_metrics(BacktestEngine(cfg).run(df, STRATEGIES[strategy](cfg)))
            m["fitness"] = fitness(m)
            m["symbol"] = symbol
            m["strategy"] = strategy
            results.append(m)

    results.sort(key=lambda r: r["fitness"], reverse=True)
    print("\n=== TOP 15 ===")
    for r in results[:15]:
        print(
            f"{r['symbol']:12} {r['strategy']:22} "
            f"{r.get('total_return_pct', 0):>6}% pf={r.get('profit_factor')} trades={r.get('total_trades')}"
        )
    champ = results[0]
    print(f"\nCHAMPION: {champ['strategy']} on {champ['symbol']} = {champ['total_return_pct']}%")


if __name__ == "__main__":
    main()
