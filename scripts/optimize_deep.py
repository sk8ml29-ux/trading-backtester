#!/usr/bin/env python3
"""Deep optimize MACD + Donchian on 30m (extended grid)."""

import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.optimize_30m import load_full, run_combo, SYMBOLS

MACD_GRID = (
    {
        "reward_risk": rr,
        "macd_strict_trend": strict,
        "swing_lookback": swing,
        "adx_trend_threshold": float(adx),
        "macd_require_below_zero": bz,
    }
    for rr, strict, swing, adx, bz in itertools.product(
        [1.25, 1.5, 1.75, 2.0, 2.5, 3.0],
        [False],
        [3, 4, 6, 8, 10, 12, 16, 20],
        [0, 15, 18],
        [False, True],
    )
)

DONCHIAN_GRID = (
    {
        "donchian_entry": entry,
        "donchian_exit": exit_p,
        "reward_risk": rr,
        "adx_trend_threshold": float(adx),
    }
    for entry, exit_p, rr, adx in itertools.product(
        [6, 8, 10, 12, 16, 20, 24, 32, 48],
        [3, 4, 6, 8, 10, 12, 16],
        [1.0, 1.25, 1.5, 2.0, 2.5, 3.0],
        [0, 15, 18, 22],
    )
    if exit_p < entry
)


def main():
    datasets = {sym: load_full(sym) for sym in SYMBOLS}
    results = {}

    for name, grid in [("macd_pullback", MACD_GRID), ("donchian_breakout", DONCHIAN_GRID)]:
        combos = list(grid)
        best_score = -1e18
        best_params = {}
        best_details = []
        print(f"\n=== DEEP {name} ({len(combos)}) ===")
        for i, params in enumerate(combos, 1):
            score, details = run_combo(name, params, datasets)
            if not details:
                continue
            # accept if at least one symbol profitable
            any_profit = any(d["total_return_pct"] > 0 and d["profit_factor"] not in (0, "0") for d in details)
            adjusted = score + (50 if any_profit else 0)
            if adjusted > best_score:
                best_score = adjusted
                best_params = params
                best_details = details
                s = " | ".join(
                    f"{d['symbol']}: {d['total_trades']}t {d['total_return_pct']}% pf={d['profit_factor']}"
                    for d in details
                )
                print(f"  [{i}] {adjusted:.1f} {params} => {s}")
        results[name] = {"best_score": best_score, "best_params": best_params, "details": best_details}

    # merge with existing RSI from optimized_30m.json
    opt_path = Path(__file__).resolve().parent.parent / "optimized_30m.json"
    existing = json.loads(opt_path.read_text()) if opt_path.exists() else []
    rsi = next((x for x in existing if x["strategy"] == "rsi_mean_reversion"), None)
    out = []
    if rsi:
        out.append(rsi)
    out.append({"strategy": "macd_pullback", **results["macd_pullback"]})
    out.append({"strategy": "donchian_breakout", **results["donchian_breakout"]})
    opt_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nUpdated {opt_path}")


if __name__ == "__main__":
    main()
