#!/usr/bin/env python3
"""Scan all strategies across universe on multiple timeframes (30m, 1h, 1d)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.data_loader import fetch_ohlcv
from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics
from backtest.mtf import apply_regime_to_entry, clamp_start_for_timeframe, prepare_entry_frame
from backtest.optimized_loader import params_for
from backtest.universe import all_symbols, category_for
from config import BacktestConfig
from scripts.optimize_30m import fitness
from strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent.parent
TIMEFRAMES = ["30m", "1h", "1d"]
REGIME_TF = "1d"


def load_pair(symbol: str, entry_tf: str):
    start = clamp_start_for_timeframe("2015-01-01", entry_tf)
    entry_df = fetch_ohlcv(symbol, entry_tf, start=start, refresh=False)
    if entry_tf == REGIME_TF:
        regime_df = entry_df
    else:
        regime_df = fetch_ohlcv(symbol, REGIME_TF, start="2015-01-01", refresh=False)
    cfg = BacktestConfig(
        symbol=symbol,
        timeframe=entry_tf,
        entry_timeframe=entry_tf,
        regime_timeframe=REGIME_TF if entry_tf != REGIME_TF else entry_tf,
    )
    return prepare_entry_frame(entry_df, cfg), regime_df


def run_one(symbol: str, strategy: str, entry_tf: str, entry_df, regime_df) -> dict | None:
    cfg = BacktestConfig(
        symbol=symbol,
        timeframe=entry_tf,
        entry_timeframe=entry_tf,
        regime_timeframe=REGIME_TF if entry_tf != REGIME_TF else entry_tf,
    )
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
    m["timeframe"] = entry_tf
    m["category"] = category_for(symbol)
    m["fitness"] = fitness(m)
    m["short_trades"] = sum(1 for t in result.trades if t.side.value == "short")
    m["long_trades"] = len(result.trades) - m["short_trades"]
    return m


def scan_timeframe(entry_tf: str) -> dict:
    symbols = all_symbols()
    strategies = list(STRATEGIES.keys())
    results: list[dict] = []
    best_per_symbol: dict[str, dict] = {}
    errors: list[str] = []

    print(f"\n{'='*60}\nTIMEFRAME: {entry_tf}\n{'='*60}")

    for i, symbol in enumerate(symbols, 1):
        try:
            entry_df, regime_df = load_pair(symbol, entry_tf)
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
            continue

        sym_best = None
        for strategy in strategies:
            m = run_one(symbol, strategy, entry_tf, entry_df, regime_df)
            if not m or m.get("error"):
                continue
            results.append(m)
            if float(m.get("total_return_pct", 0)) <= 0:
                continue
            if sym_best is None or m["fitness"] > sym_best["fitness"]:
                sym_best = m

        if sym_best:
            best_per_symbol[symbol] = sym_best

        if i % 10 == 0 or i == len(symbols):
            prof = len([s for s in best_per_symbol if float(best_per_symbol[s].get("total_return_pct", 0)) > 0])
            print(f"  [{i}/{len(symbols)}] {prof} profitable symbols so far")

    profitable = [v for v in best_per_symbol.values() if float(v.get("total_return_pct", 0)) > 0]
    total_trades = sum(int(v.get("total_trades", 0)) for v in profitable)
    avg_wr = (
        sum(float(v.get("win_rate_pct", 0)) for v in profitable) / len(profitable) if profitable else 0
    )

    summary = {
        "timeframe": entry_tf,
        "symbols_scanned": len(symbols),
        "profitable_symbols": len(profitable),
        "total_best_trades": total_trades,
        "avg_win_rate_pct": round(avg_wr, 1),
        "top_5": sorted(profitable, key=lambda x: -x["fitness"])[:5],
    }

    out = {
        "scanned_at": time.strftime("%Y-%m-%d %H:%M"),
        "timeframe": entry_tf,
        "strategies": strategies,
        "results": results,
        "best_per_symbol": best_per_symbol,
        "summary": summary,
        "errors": errors,
    }
    path = ROOT / f"universe_scan_{entry_tf}.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"Saved {path} — {len(profitable)}/{len(symbols)} profitable, ~{total_trades} trades")
    return summary


def main():
    t0 = time.time()
    all_summaries = []
    for tf in TIMEFRAMES:
        all_summaries.append(scan_timeframe(tf))

    combined = {
        "scanned_at": time.strftime("%Y-%m-%d %H:%M"),
        "timeframes": TIMEFRAMES,
        "summaries": all_summaries,
    }
    out_path = ROOT / "universe_scan_multi_tf.json"
    out_path.write_text(json.dumps(combined, indent=2, default=str), encoding="utf-8")

    print(f"\n{'='*60}\nJÄMFÖRELSE TIMEFRAMES\n{'='*60}")
    for s in all_summaries:
        print(
            f"  {s['timeframe']:4}  {s['profitable_symbols']:2}/{s['symbols_scanned']} lönsamma  "
            f"{s['total_best_trades']:4} trades  snitt WR {s['avg_win_rate_pct']}%"
        )
        if s["top_5"]:
            top = s["top_5"][0]
            print(
                f"       bäst: {top['symbol']} / {top['strategy']} "
                f"{top.get('total_return_pct', 0):+.1f}%"
            )
    print(f"\nTotal tid: {time.time()-t0:.0f}s — se {out_path}")


if __name__ == "__main__":
    main()
