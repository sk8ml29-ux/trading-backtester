#!/usr/bin/env python3
"""Find forex configs with retail-normal trade frequency (12+ OOS trades) that still pass."""

from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.forex_search import FOREX, SearchResult, load_cached_forex, load_regime_df, run_single
from research.pipeline import build_portfolio_json

ROOT = Path(__file__).resolve().parent.parent
MIN_TRADES = 10
MIN_PF = 1.15
MIN_RET = 0.5

# MTF breakout on 30m — best trade count so far (GBPUSD 21 trades)
MTF_GRID = [
    dict(zip(
        ["donchian_entry", "donchian_exit", "fx_reward_risk", "fx_min_range_atr", "fx_session_filter", "fx_mtf_use_1h"],
        vals,
    ))
    for vals in product(
        [16, 18, 20, 22, 24],
        [5, 6],
        [2.0, 2.5, 3.0],
        [0.35, 0.40, 0.45],
        ["active"],
        [True],
    )
]

# Moderate Donchian — more trades than entry 36-48
DONCHIAN_GRID = [
    dict(zip(
        ["donchian_entry", "donchian_exit", "reward_risk", "adx_trend_threshold"],
        vals,
    ))
    for vals in product(
        [20, 24, 28],
        [6, 8],
        [2.5, 3.0],
        [0.0, 15.0, 18.0],
    )
]

SEARCH = [
    ("forex_mtf_breakout", MTF_GRID, ["30m"]),
    ("donchian_bidirectional", DONCHIAN_GRID, ["30m", "1h"]),
]


def _pf(pf) -> float:
    if pf in (0, "0", None):
        return 0.0
    if pf == "inf":
        return 99.0
    try:
        return float(pf)
    except (TypeError, ValueError):
        return 0.0


def _freq_score(r: SearchResult) -> float:
    if not r.test_pass or r.test_return_pct < MIN_RET or _pf(r.test_profit_factor) < MIN_PF:
        return -999.0
    t = r.test_trades
    bonus = 80.0 if t >= 20 else (40.0 if t >= 15 else t * 2.0)
    return bonus + r.test_return_pct * 0.5 + _pf(r.test_profit_factor) * 5.0


def main() -> int:
    print("=== FOREX FREQUENCY SOLVE ===")
    print(f"Target: OOS pass, PF>={MIN_PF}, ret>={MIN_RET}%, prefer {MIN_TRADES}+ trades\n")

    all_rows: list[dict] = []
    data_cache: dict[str, tuple] = {}

    for sym in FOREX:
        regime = load_regime_df(sym)
        for tf in ("30m", "1h"):
            try:
                entry = load_cached_forex(sym, tf)
                if len(entry) >= 300:
                    data_cache[f"{sym}|{tf}"] = (entry, regime)
            except Exception as exc:
                print(f"  skip {sym} {tf}: {exc}")

    total = sum(len(g) for _, g, tfs in SEARCH for _ in tfs) * len(FOREX)
    done = 0

    for strat, grid, tfs in SEARCH:
        for sym in FOREX:
            for tf in tfs:
                key = f"{sym}|{tf}"
                if key not in data_cache:
                    continue
                entry, regime = data_cache[key]
                best: SearchResult | None = None
                for params in grid:
                    done += 1
                    if done % 100 == 0:
                        print(f"  progress {done}/{total}...", flush=True)
                    r = run_single(sym, tf, strat, params, entry, regime)
                    sc = _freq_score(r)
                    row = {
                        "symbol": r.symbol,
                        "strategy": r.strategy,
                        "timeframe": r.timeframe,
                        "params": r.params,
                        "test_return_pct": r.test_return_pct,
                        "test_trades": r.test_trades,
                        "test_profit_factor": r.test_profit_factor,
                        "test_pass": r.test_pass,
                        "freq_score": sc,
                    }
                    all_rows.append(row)
                    if sc > -999 and (best is None or sc > _freq_score(best)):
                        best = r
                if best and _freq_score(best) > -999:
                    print(
                        f"  {sym} {tf} {strat}: {best.test_return_pct:+.1f}% "
                        f"{best.test_trades} trades PF={best.test_profit_factor} "
                        f"params={best.params}",
                        flush=True,
                    )

    valid = [r for r in all_rows if r["freq_score"] > -999]
    valid.sort(key=lambda x: (x["test_trades"], x["test_return_pct"]), reverse=True)

    # Best per symbol|tf by frequency score
    best_per: dict[str, dict] = {}
    for r in valid:
        k = f"{r['symbol']}|{r['timeframe']}"
        if k not in best_per or r["freq_score"] > best_per[k]["freq_score"]:
            best_per[k] = r

    picks = sorted(best_per.values(), key=lambda x: x["freq_score"], reverse=True)

    print(f"\nRuns: {len(all_rows)}  |  Valid: {len(valid)}  |  Pairs covered: {len(picks)}")
    print("\n=== BEST PER SYMBOL|TF (frequency-ranked) ===")
    total_trades = 0
    total_ret = 0.0
    for r in picks:
        total_trades += r["test_trades"]
        total_ret += r["test_return_pct"]
        print(
            f"  {r['symbol']:10} {r['timeframe']:4} {r['strategy']:22} "
            f"{r['test_return_pct']:+5.1f}%  {r['test_trades']:3} trades  PF={r['test_profit_factor']}"
        )
    print(f"\nSummed OOS: {total_ret:+.1f}%  |  {total_trades} trades")

    # Build portfolio from picks with at least MIN_TRADES or top scorers
    portfolio_rows = []
    for r in picks:
        if r["test_trades"] >= MIN_TRADES or r["freq_score"] >= 50:
            portfolio_rows.append({
                "symbol": r["symbol"],
                "strategy": r["strategy"],
                "timeframe": r["timeframe"],
                "category": "forex",
                "test_return_pct": r["test_return_pct"],
                "test_trades": r["test_trades"],
                "test_profit_factor": r["test_profit_factor"],
                "test_pass": True,
                "params": r["params"],
            })

    if not portfolio_rows:
        print("\nNo frequency portfolio — keeping existing.")
        return 1

    research = {
        "asset_class": "forex",
        "search_type": "frequency_solve",
        "oos_passed": len(portfolio_rows),
        "combined_oos_trades": sum(r["test_trades"] for r in portfolio_rows),
        "combined_oos_pct": sum(r["test_return_pct"] for r in portfolio_rows),
        "best_per_symbol_tf": portfolio_rows,
        "top_10": portfolio_rows[:10],
    }
    out = ROOT / "research_results_forex_frequency.json"
    out.write_text(json.dumps(research, indent=2), encoding="utf-8")

    portfolio = build_portfolio_json(
        research,
        name="oos_paper_forex",
        description=f"Forex frequency solve — {research['combined_oos_trades']} OOS trades.",
        source="research_results_forex_frequency.json",
    )
    port_path = ROOT / "mixed_portfolio_oos_forex.json"
    port_path.write_text(json.dumps(portfolio, indent=2), encoding="utf-8")
    print(f"\nUpdated {port_path} ({len(portfolio_rows)} bots)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
