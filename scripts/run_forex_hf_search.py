#!/usr/bin/env python3
"""Run forex HF search with live progress (unbuffered)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.forex_search import (
    FOREX,
    FOREX_SEARCH_STRATEGIES,
    FOREX_TIMEFRAMES,
    PARAM_GRIDS,
    load_cached_forex,
    load_regime_df,
    run_single,
    save_winners,
    _score_hf,
    MIN_HF_OOS_TRADES,
    DEFAULT_START,
)
from research.pipeline import FOREX as FX


def main() -> int:
    start = DEFAULT_START
    symbols = FOREX
    timeframes = FOREX_TIMEFRAMES
    strategies = FOREX_SEARCH_STRATEGIES

    data_cache: dict = {}
    for sym in symbols:
        regime_df = load_regime_df(sym)
        for tf in timeframes:
            try:
                entry_df = load_cached_forex(sym, tf, start)
                if len(entry_df) >= 300:
                    data_cache[f"{sym}|{tf}"] = (entry_df, regime_df)
                    print(f"Loaded {sym} {tf}: {len(entry_df)} bars", flush=True)
            except Exception as exc:
                print(f"SKIP {sym} {tf}: {exc}", flush=True)

    all_rows = []
    run_n = 0
    total = sum(
        len(PARAM_GRIDS.get(s, [{}])) * len(symbols) * len(timeframes)
        for s in strategies
    )

    for strat_name in strategies:
        grid = PARAM_GRIDS.get(strat_name, [{}])
        for sym in symbols:
            for tf in timeframes:
                if strat_name == "forex_harmonic" and tf == "15m":
                    continue
                key = f"{sym}|{tf}"
                if key not in data_cache:
                    continue
                entry_df, regime_df = data_cache[key]
                for params in grid:
                    run_n += 1
                    r = run_single(sym, tf, strat_name, params, entry_df, regime_df)
                    row = {
                        "symbol": r.symbol,
                        "strategy": r.strategy,
                        "timeframe": r.timeframe,
                        "params": r.params,
                        "category": "forex",
                        "provider": "dukascopy_resampled" if tf != "15m" else "dukascopy",
                        "bars": r.bars,
                        "split_date": r.split_date,
                        "train_return_pct": r.train_return_pct,
                        "test_return_pct": r.test_return_pct,
                        "test_trades": r.test_trades,
                        "test_win_rate_pct": r.test_win_rate_pct,
                        "test_profit_factor": r.test_profit_factor,
                        "test_pass": r.test_pass,
                        "score": r.score,
                    }
                    if r.error:
                        row["error"] = r.error
                    all_rows.append(row)
                    status = "PASS" if r.test_pass else "fail"
                    if r.test_pass:
                        print(
                            f"[{run_n}] {status} {sym} {tf} {strat_name} "
                            f"ret={r.test_return_pct:+.1f}% trades={r.test_trades} PF={r.test_profit_factor}",
                            flush=True,
                        )
                    elif run_n % 25 == 0:
                        print(f"[{run_n}/{total}] ...", flush=True)

    valid = [r for r in all_rows if r.get("test_pass") and "error" not in r]
    valid.sort(key=lambda x: x.get("score", -999), reverse=True)
    hf_valid = [r for r in valid if r.get("test_trades", 0) >= MIN_HF_OOS_TRADES]
    hf_valid.sort(key=lambda x: x.get("score", -999), reverse=True)

    best_per_key: dict = {}
    pool = hf_valid if hf_valid else valid
    for r in pool:
        key = f"{r['symbol']}|{r['timeframe']}"
        if key not in best_per_key or r["score"] > best_per_key[key]["score"]:
            best_per_key[key] = r

    out = {
        "asset_class": "forex",
        "symbols": symbols,
        "timeframes": timeframes,
        "strategies": strategies,
        "default_start": start,
        "min_hf_oos_trades": MIN_HF_OOS_TRADES,
        "data_source": "dukascopy 15m cache (native 15m + resampled 30m/1h)",
        "total_runs": len(all_rows),
        "oos_passed": len(valid),
        "hf_oos_passed": len(hf_valid),
        "top_10": valid[:10],
        "top_10_hf": hf_valid[:10],
        "best_per_symbol_tf": list(best_per_key.values()),
        "near_misses": [],
        "all_results": all_rows,
    }

    results_path, port_path = save_winners(out)
    print(f"\nDone. Runs={len(all_rows)} OOS={len(valid)} HF={len(hf_valid)}", flush=True)
    print(f"Saved {results_path} / {port_path}", flush=True)

    if hf_valid:
        print("\n=== HF WINNERS ===", flush=True)
        for r in hf_valid[:15]:
            print(
                f"  {r['symbol']:10} {r['timeframe']:4} {r['strategy']:22} "
                f"{r['test_return_pct']:+.2f}%  {r['test_trades']} trades  PF {r['test_profit_factor']}",
                flush=True,
            )
    elif valid:
        print("\n=== ALL OOS WINNERS (no 20+ trade passes) ===", flush=True)
        for r in valid[:15]:
            print(
                f"  {r['symbol']:10} {r['timeframe']:4} {r['strategy']:22} "
                f"{r['test_return_pct']:+.2f}%  {r['test_trades']} trades  PF {r['test_profit_factor']}",
                flush=True,
            )
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
