#!/usr/bin/env python3
"""
Scan all strategies across multi-asset universe (forex, stocks, crypto, commodities).
Saves ranked results to universe_scan_30m.json.
"""

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
from backtest.optimized_loader import apply_to_config, params_for
from backtest.universe import UNIVERSE, all_symbols, category_for
from config import BacktestConfig
from scripts.optimize_30m import fitness
from strategies import STRATEGIES

ENTRY_TF = "30m"
MIN_TRADES = 3


def load_pair(symbol: str):
    entry_df = fetch_ohlcv(symbol, ENTRY_TF, refresh=False)
    regime_df = fetch_ohlcv(symbol, "1d", refresh=False)
    cfg = BacktestConfig(symbol=symbol)
    return prepare_entry_frame(entry_df, cfg), regime_df


def run_symbol_strategy(symbol: str, strategy: str, entry_df, regime_df) -> dict | None:
    cfg = BacktestConfig(
        symbol=symbol,
        timeframe=ENTRY_TF,
        entry_timeframe=ENTRY_TF,
        regime_timeframe="1d",
    )
    known = params_for(symbol, strategy)
    if known:
        for k, v in known.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    elif strategy == "rsi_mean_reversion" and not known:
        cfg.adx_trend_threshold = 25.0

    df = apply_regime_to_entry(entry_df, regime_df, cfg)
    try:
        m = compute_metrics(BacktestEngine(cfg).run(df, STRATEGIES[strategy](cfg)))
    except Exception as exc:
        return {"symbol": symbol, "strategy": strategy, "error": str(exc)}

    m["symbol"] = symbol
    m["strategy"] = strategy
    m["category"] = category_for(symbol)
    m["fitness"] = fitness(m)
    return m


def main():
    symbols = all_symbols()
    strategies = list(STRATEGIES.keys())
    print(f"Scanning {len(symbols)} symbols x {len(strategies)} strategies on {ENTRY_TF}\n")

    results: list[dict] = []
    errors: list[str] = []
    t0 = time.time()

    for i, symbol in enumerate(symbols, 1):
        cat = category_for(symbol)
        print(f"[{i}/{len(symbols)}] {symbol} ({cat})")
        try:
            entry_df, regime_df = load_pair(symbol)
        except Exception as exc:
            errors.append(f"{symbol}: load failed — {exc}")
            print(f"  skip: {exc}")
            continue

        for strategy in strategies:
            m = run_symbol_strategy(symbol, strategy, entry_df, regime_df)
            if m is None:
                continue
            if "error" in m:
                errors.append(f"{symbol}/{strategy}: {m['error']}")
                continue
            results.append(m)
            ret = m.get("total_return_pct", 0)
            pf = m.get("profit_factor", 0)
            trades = m.get("total_trades", 0)
            mark = "+" if ret > 0 and pf not in (0, "0") and float(pf) >= 1.0 else " "
            print(f"  {mark} {strategy}: {trades}t {ret}% pf={pf}")

    profitable = [
        r for r in results
        if r.get("total_trades", 0) >= MIN_TRADES
        and r.get("total_return_pct", 0) > 0
        and r.get("profit_factor") not in (0, "0", 0.0)
        and float(r.get("profit_factor", 0)) >= 1.0
    ]
    profitable.sort(key=lambda r: r["fitness"], reverse=True)

    by_category: dict[str, list] = {}
    for r in profitable:
        by_category.setdefault(r["category"], []).append(r)

    best_per_symbol: dict[str, dict] = {}
    for r in results:
        sym = r["symbol"]
        if r.get("total_trades", 0) < MIN_TRADES:
            continue
        if sym not in best_per_symbol or r["fitness"] > best_per_symbol[sym]["fitness"]:
            best_per_symbol[sym] = r

    out = {
        "timeframe": ENTRY_TF,
        "scanned_symbols": len(symbols),
        "total_runs": len(results),
        "profitable_count": len(profitable),
        "top_20": profitable[:20],
        "best_per_symbol": {
            sym: {
                "strategy": r["strategy"],
                "category": r["category"],
                "total_return_pct": r["total_return_pct"],
                "profit_factor": r["profit_factor"],
                "total_trades": r["total_trades"],
                "fitness": r["fitness"],
            }
            for sym, r in sorted(best_per_symbol.items())
        },
        "by_category_best": {
            cat: max(rows, key=lambda r: r["fitness"])
            for cat, rows in by_category.items()
        },
        "errors": errors,
    }

    out_path = Path(__file__).resolve().parent.parent / "universe_scan_30m.json"
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    print(f"\nDone in {time.time()-t0:.0f}s — {len(profitable)} profitable combos")
    print(f"Saved {out_path}\n")
    print("=== TOP 10 ===")
    for r in profitable[:10]:
        print(
            f"  {r['symbol']:12} ({r['category']:12}) {r['strategy']:25} "
            f"{r['total_return_pct']:>6}% pf={r['profit_factor']} trades={r['total_trades']}"
        )


if __name__ == "__main__":
    main()
