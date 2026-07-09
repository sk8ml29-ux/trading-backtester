#!/usr/bin/env python3
"""Scan all strategies (incl. bidirectional/short) across universe."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.data_loader import fetch_ohlcv
from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics
from backtest.mtf import apply_regime_to_entry, prepare_entry_frame
from backtest.optimized_loader import params_for
from backtest.universe import all_symbols, category_for
from config import BacktestConfig
from scripts.optimize_30m import fitness
from strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent.parent
ENTRY_TF = "30m"


def run_one(symbol: str, strategy: str, entry_df, regime_df) -> dict | None:
    cfg = BacktestConfig(symbol=symbol, timeframe=ENTRY_TF, entry_timeframe=ENTRY_TF)
    for k, v in params_for(symbol, strategy).items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    if strategy in ("rsi_mean_reversion", "rsi_bidirectional") and cfg.adx_trend_threshold == 0:
        cfg.adx_trend_threshold = 25.0

    df = apply_regime_to_entry(entry_df, regime_df, cfg)
    try:
        result = BacktestEngine(cfg).run(df, STRATEGIES[strategy](cfg))
        m = compute_metrics(result)
    except Exception as exc:
        return {"symbol": symbol, "strategy": strategy, "error": str(exc)}

    m["symbol"] = symbol
    m["strategy"] = strategy
    m["category"] = category_for(symbol)
    m["fitness"] = fitness(m)
    m["short_trades"] = sum(1 for t in result.trades if t.side.value == "short")
    m["long_trades"] = len(result.trades) - m["short_trades"]
    return m


def main():
    symbols = all_symbols()
    strategies = list(STRATEGIES.keys())
    print(f"Extended scan: {len(symbols)} symbols x {len(strategies)} strategies\n")

    results: list[dict] = []
    best_per_symbol: dict[str, dict] = {}
    t0 = time.time()

    for i, symbol in enumerate(symbols, 1):
        print(f"[{i}/{len(symbols)}] {symbol}")
        try:
            entry_df = prepare_entry_frame(fetch_ohlcv(symbol, ENTRY_TF), BacktestConfig(symbol=symbol))
            regime_df = fetch_ohlcv(symbol, "1d")
        except Exception as exc:
            print(f"  skip load: {exc}")
            continue

        sym_best = None
        for strategy in strategies:
            m = run_one(symbol, strategy, entry_df, regime_df)
            if not m or m.get("error"):
                continue
            results.append(m)
            ret = float(m.get("total_return_pct", 0))
            if ret <= 0:
                continue
            if sym_best is None or m["fitness"] > sym_best["fitness"]:
                sym_best = m

        if sym_best:
            best_per_symbol[symbol] = sym_best
            s = sym_best
            print(
                f"  best: {s['strategy']:24} ret={s['total_return_pct']:+.1f}% "
                f"PF={s.get('profit_factor')} L={s.get('long_trades',0)} S={s.get('short_trades',0)}"
            )

    out = {
        "scanned_at": time.strftime("%Y-%m-%d %H:%M"),
        "strategies": strategies,
        "results": results,
        "best_per_symbol": best_per_symbol,
    }
    path = ROOT / "universe_scan_extended.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved {path} ({len(results)} results, {time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
