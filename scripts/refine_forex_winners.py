#!/usr/bin/env python3
"""Refine forex Donchian params around OOS winners — update portfolio if better."""

from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.forex_search import SearchResult, load_cached_forex, load_regime_df, run_single
from research.pipeline import build_portfolio_json, save_research

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "research_results_forex_winners.json"
PROGRESS_EVERY = 20

# Focused grid around current AUDUSD 30m winner (36/10/3.0/22) — ~240 combos
AUDUSD_30M_GRID = {
    "donchian_entry": [32, 34, 36, 38, 40],
    "donchian_exit": [8, 10, 12],
    "reward_risk": [2.8, 3.0, 3.2, 3.5],
    "adx_trend_threshold": [20.0, 22.0, 24.0, 25.0],
}

# Lighter grid for marginal 1h pairs
MARGINAL_1H_GRID = {
    "donchian_entry": [44, 48, 52],
    "donchian_exit": [10, 12, 14],
    "reward_risk": [3.0, 3.2, 3.5],
    "adx_trend_threshold": [18.0, 20.0, 22.0],
}

FOCUS: list[tuple[str, str, dict]] = [
    ("AUDUSD=X", "30m", AUDUSD_30M_GRID),
    ("AUDUSD=X", "1h", MARGINAL_1H_GRID),
    ("USDCAD=X", "1h", MARGINAL_1H_GRID),
    ("USDJPY=X", "1h", MARGINAL_1H_GRID),
]


def _combos(grid: dict) -> list[dict]:
    keys = list(grid.keys())
    return [dict(zip(keys, vals)) for vals in product(*(grid[k] for k in keys))]


def _load_existing() -> dict[str, SearchResult]:
    if not RESULTS_PATH.exists():
        return {}
    data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    out: dict[str, SearchResult] = {}
    for row in data.get("best_per_symbol_tf", []):
        if not row.get("test_pass"):
            continue
        key = f"{row['symbol']}|{row['timeframe']}"
        out[key] = SearchResult(
            symbol=row["symbol"],
            strategy=row["strategy"],
            timeframe=row["timeframe"],
            params=row.get("params") or {},
            bars=int(row.get("bars", 0)),
            split_date=str(row.get("split_date", "")),
            train_return_pct=float(row.get("train_return_pct", 0)),
            test_return_pct=float(row.get("test_return_pct", 0)),
            test_trades=int(row.get("test_trades", 0)),
            test_win_rate_pct=float(row.get("test_win_rate_pct", 0)),
            test_profit_factor=row.get("test_profit_factor", 0),
            test_pass=True,
            score=float(row.get("score", 0)),
        )
    return out


def _search_pair(
    symbol: str,
    tf: str,
    grid: dict,
    baseline: SearchResult | None,
) -> SearchResult | None:
    combos = _combos(grid)
    entry_df = load_cached_forex(symbol, tf)
    regime_df = load_regime_df(symbol)
    best = baseline if baseline and baseline.test_pass else None
    n = len(combos)

    print(f"\n  {symbol} {tf}: {n} combos", end="", flush=True)
    if best:
        print(
            f"  (baseline {best.test_return_pct:+.1f}% PF {best.test_profit_factor} score {best.score:.1f})",
            flush=True,
        )
    else:
        print(flush=True)

    for i, params in enumerate(combos, 1):
        r = run_single(symbol, tf, "donchian_bidirectional", params, entry_df, regime_df)
        if r.test_pass and (best is None or r.score > best.score):
            best = r
        if i % PROGRESS_EVERY == 0 or i == n:
            tag = ""
            if best and best.test_pass:
                tag = f" best {best.test_return_pct:+.1f}% PF {best.test_profit_factor}"
            print(f"    [{i}/{n}]{tag}", flush=True)

    if best and baseline and best.score <= baseline.score:
        print(f"  -> kept baseline ({baseline.test_return_pct:+.1f}%)", flush=True)
        return baseline
    if best:
        improved = baseline is None or best.score > (baseline.score if baseline else -999)
        mark = "IMPROVED" if improved else "kept"
        print(
            f"  -> {mark}: test {best.test_return_pct:+.1f}%  "
            f"PF {best.test_profit_factor}  {best.test_trades} trades  {best.params}",
            flush=True,
        )
    else:
        print("  -> no pass in refine grid", flush=True)
    return best


def _row_from_result(r: SearchResult) -> dict:
    return {
        "symbol": r.symbol,
        "strategy": r.strategy,
        "timeframe": r.timeframe,
        "category": "forex",
        "provider": "dukascopy",
        "bars": r.bars,
        "split_date": r.split_date,
        "train_return_pct": r.train_return_pct,
        "test_return_pct": r.test_return_pct,
        "test_trades": r.test_trades,
        "test_win_rate_pct": r.test_win_rate_pct,
        "test_profit_factor": r.test_profit_factor,
        "test_pass": True,
        "score": r.score,
        "params": r.params,
    }


def main() -> int:
    total_combos = sum(len(_combos(g)) for _, _, g in FOCUS)
    print(f"=== FOREX REFINE ({len(FOCUS)} pairs, {total_combos} total combos) ===\n")

    existing = _load_existing()
    updated: dict[str, SearchResult] = dict(existing)
    any_improved = False

    for symbol, tf, grid in FOCUS:
        key = f"{symbol}|{tf}"
        baseline = existing.get(key)
        result = _search_pair(symbol, tf, grid, baseline)
        if result and result.test_pass:
            if key not in existing or result.score > existing[key].score:
                any_improved = True
            updated[key] = result

    winners = [updated[k] for k in sorted(updated) if updated[k].test_pass]
    if not winners:
        print("\nNo OOS passes — keeping existing portfolio.")
        return 0

    rows = [_row_from_result(w) for w in winners]
    rows.sort(key=lambda x: x["score"], reverse=True)

    out = {
        "asset_class": "forex",
        "refined": True,
        "oos_passed": len(rows),
        "top_10": rows[:10],
        "best_per_symbol_tf": rows,
        "all_results": rows,
    }
    save_research(out, RESULTS_PATH)

    portfolio = build_portfolio_json(
        out,
        name="oos_paper_forex",
        description="Forex OOS winners (refined Donchian + ADX).",
        source="research_results_forex_winners.json",
    )
    port_path = ROOT / "mixed_portfolio_oos_forex.json"
    port_path.write_text(json.dumps(portfolio, indent=2), encoding="utf-8")

    status = "updated with improvements" if any_improved else "rebuilt (no score change)"
    print(f"\n{status}: {port_path}  ({len(portfolio['pairs'])} pairs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
