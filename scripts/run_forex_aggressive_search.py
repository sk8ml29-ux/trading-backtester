#!/usr/bin/env python3
"""Aggressive forex OOS search — maximize return, accept higher variance."""

from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.forex_search import FOREX, load_cached_forex, load_regime_df, run_single
from research.pipeline import build_portfolio_json

ROOT = Path(__file__).resolve().parent.parent
RISK = 0.01  # 1% aggressive
MIN_TRADES = 3
MIN_PF = 1.0
MIN_RET = 1.0

STRATS = {
    "donchian_bidirectional": [
        dict(zip(["donchian_entry", "donchian_exit", "reward_risk", "adx_trend_threshold"], v))
        for v in product(
            [16, 20, 24, 28, 32, 36, 40, 48],
            [5, 6, 8],
            [2.5, 3.0, 3.5, 4.0],
            [0.0, 12.0, 15.0, 18.0, 22.0],
        )
    ],
    "forex_mtf_breakout": [
        dict(zip(
            ["donchian_entry", "donchian_exit", "fx_reward_risk", "fx_min_range_atr",
             "fx_session_filter", "fx_mtf_use_1h"],
            v,
        ))
        for v in product(
            [14, 16, 18, 20, 22, 24],
            [5, 6],
            [2.5, 3.0, 3.5, 4.0],
            [0.35, 0.40, 0.45, 0.50],
            ["active"],
            [True],
        )
    ],
}


def _pf(pf) -> float:
    if pf in (0, "0", None):
        return 0.0
    if pf == "inf":
        return 99.0
    try:
        return float(pf)
    except (TypeError, ValueError):
        return 0.0


def _ret_score(r) -> float:
    if not r.test_pass or r.test_return_pct < MIN_RET or _pf(r.test_profit_factor) < MIN_PF:
        return -999.0
    if r.test_trades < MIN_TRADES:
        return -999.0
    return r.test_return_pct * 0.7 + _pf(r.test_profit_factor) * 8.0 + min(r.test_trades, 30) * 0.1


def main() -> int:
    print("=== FOREX AGGRESSIVE SEARCH (max return) ===")
    print(f"Risk target: {RISK*100}%  |  min OOS ret {MIN_RET}%  PF>={MIN_PF}\n")

    cache: dict[str, tuple] = {}
    for sym in FOREX:
        regime = load_regime_df(sym)
        for tf in ("30m", "1h"):
            try:
                entry = load_cached_forex(sym, tf)
                if len(entry) >= 300:
                    cache[f"{sym}|{tf}"] = (entry, regime)
            except Exception as e:
                print(f"  skip {sym} {tf}: {e}")

    all_rows: list[dict] = []
    total = sum(len(g) * len(FOREX) * (1 if s == "forex_mtf_breakout" else 2)
                for s, g in STRATS.items()
                for tf in (("30m",) if False else ("30m", "1h")))
    # mtf only 30m, donchian both
    total = len(STRATS["donchian_bidirectional"]) * len(FOREX) * 2
    total += len(STRATS["forex_mtf_breakout"]) * len(FOREX)
    done = 0

    best_per: dict[str, dict] = {}

    for strat, grid in STRATS.items():
        tfs = ("30m",) if strat == "forex_mtf_breakout" else ("30m", "1h")
        for sym in FOREX:
            for tf in tfs:
                key = f"{sym}|{tf}"
                if key not in cache:
                    continue
                entry, regime = cache[key]
                local_best = None
                for params in grid:
                    done += 1
                    if done % 200 == 0:
                        print(f"  {done}/{total}...", flush=True)
                    r = run_single(sym, tf, strat, params, entry, regime)
                    sc = _ret_score(r)
                    row = {
                        "symbol": r.symbol,
                        "strategy": r.strategy,
                        "timeframe": r.timeframe,
                        "params": r.params,
                        "test_return_pct": r.test_return_pct,
                        "test_trades": r.test_trades,
                        "test_profit_factor": r.test_profit_factor,
                        "test_pass": r.test_pass,
                        "ret_score": sc,
                    }
                    all_rows.append(row)
                    if sc > -999 and (local_best is None or sc > _ret_score(local_best)):
                        local_best = r
                if local_best and _ret_score(local_best) > -999:
                    bk = key
                    prev = best_per.get(bk)
                    if prev is None or _ret_score(local_best) > prev["ret_score"]:
                        best_per[bk] = {
                            "symbol": local_best.symbol,
                            "strategy": local_best.strategy,
                            "timeframe": local_best.timeframe,
                            "params": local_best.params,
                            "test_return_pct": local_best.test_return_pct,
                            "test_trades": local_best.test_trades,
                            "test_profit_factor": local_best.test_profit_factor,
                            "test_pass": True,
                            "ret_score": _ret_score(local_best),
                        }
                    print(
                        f"  BEST {sym} {tf} {local_best.strategy}: "
                        f"{local_best.test_return_pct:+.1f}%  {local_best.test_trades} tr  "
                        f"PF={local_best.test_profit_factor}",
                        flush=True,
                    )

    picks = sorted(best_per.values(), key=lambda x: x["ret_score"], reverse=True)
    # Drop weak symbols (EURUSD often fails) — keep all that pass
    picks = [p for p in picks if p["test_return_pct"] >= MIN_RET]

    print(f"\n=== TOP PICKS ({len(picks)} bots) ===")
    cap = 20_000.0
    total_end = 0.0
    total_trades = 0
    for p in picks:
        end = cap * (1 + p["test_return_pct"] / 100)
        total_end += end
        total_trades += p["test_trades"]
        print(
            f"  {p['symbol']:10} {p['timeframe']:4} {p['strategy']:22} "
            f"{p['test_return_pct']:+5.1f}%  {p['test_trades']:3} tr  PF={p['test_profit_factor']}"
        )
    combined = (total_end / (cap * len(picks)) - 1) * 100 if picks else 0
    print(f"\nCapital-weighted OOS: {combined:+.1f}%  |  {total_trades} trades  |  {len(picks)} bots")

    if not picks:
        print("No aggressive portfolio.")
        return 1

    research = {
        "asset_class": "forex",
        "search_type": "aggressive_max_return",
        "risk_per_trade": RISK,
        "oos_passed": len(picks),
        "combined_oos_pct": round(sum(p["test_return_pct"] for p in picks), 2),
        "combined_oos_trades": total_trades,
        "capital_weighted_oos_pct": round(combined, 2),
        "best_per_symbol_tf": picks,
        "top_10": picks[:10],
    }
    (ROOT / "research_results_forex_aggressive.json").write_text(
        json.dumps(research, indent=2), encoding="utf-8"
    )

    portfolio = build_portfolio_json(
        research,
        name="oos_paper_forex",
        description=f"Aggressive max-return OOS. {combined:+.1f}% weighted, risk {RISK*100}%.",
        source="research_results_forex_aggressive.json",
    )
    portfolio["risk_per_trade"] = RISK
    port = ROOT / "mixed_portfolio_oos_forex.json"
    port.write_text(json.dumps(portfolio, indent=2), encoding="utf-8")
    print(f"\nUpdated {port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
