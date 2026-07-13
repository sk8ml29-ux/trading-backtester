#!/usr/bin/env python3
"""
Steg 1: Parameteroptimering per symbol via OOS walk-forward.

Testar kombinationer av reward_risk och swing_lookback för varje symbol
och hittar den kombination som ger bäst OOS-resultat (inte in-sample).
Sparar de bästa parametrarna till optimized_params.json.
"""

from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.data_loader import fetch_ohlcv
from config import BacktestConfig
from research.walk_forward import run_walk_forward
from strategies.macd_pullback import MacdPullbackStrategy

ROOT = Path(__file__).resolve().parent.parent

SYMBOLS = ["NEAR-USD", "ATOM-USD", "BNB-USD", "XRP-USD"]
TIMEFRAME = "1h"
START = "2024-01-01"

# Parametergrid — hålls liten för snabbhet men täcker det viktiga
REWARD_RISKS   = [1.5, 2.0, 2.5, 3.0]
SWING_LOOKBACKS = [8, 12, 16, 20, 24]
SIGNAL_MODES   = ["cross_below_zero", "histogram_flip", "either"]


def _score(m: dict) -> float:
    """Kombinerat OOS-poäng: sharpe × profit_factor × log(trades+1)."""
    pf    = float(m.get("profit_factor", 0) or 0)
    sh    = float(m.get("sharpe", 0) or 0)
    tr    = int(m.get("total_trades", 0) or 0)
    ret   = float(m.get("total_return_pct", 0) or 0)
    if pf <= 0 or sh <= 0 or ret <= 0 or tr < 15:
        return -1.0
    import math
    return sh * pf * math.log(tr + 1)


def optimize_symbol(symbol: str) -> dict:
    print(f"\n{'='*55}")
    print(f"  Optimerar: {symbol}")
    print(f"{'='*55}")

    entry_df  = fetch_ohlcv(symbol, TIMEFRAME, start=START, refresh=False)
    regime_df = fetch_ohlcv(symbol, "1d", start="2022-01-01", refresh=False)

    best_score  = -99.0
    best_params = {}
    best_metrics = {}
    results = []

    total = len(REWARD_RISKS) * len(SWING_LOOKBACKS) * len(SIGNAL_MODES)
    done  = 0

    for rr, sl, mode in product(REWARD_RISKS, SWING_LOOKBACKS, SIGNAL_MODES):
        done += 1
        cfg = BacktestConfig(
            symbol=symbol,
            timeframe=TIMEFRAME,
            entry_timeframe=TIMEFRAME,
            regime_timeframe="1d",
            reward_risk=rr,
            swing_lookback=sl,
            macd_signal_mode=mode,
        )
        strategy = MacdPullbackStrategy(cfg)

        try:
            wf = run_walk_forward(entry_df, regime_df, cfg, strategy)
            score = _score(wf.test_metrics)
            results.append({
                "reward_risk": rr,
                "swing_lookback": sl,
                "signal_mode": mode,
                "oos_pass": wf.test_pass,
                "oos_pf": round(float(wf.test_metrics.get("profit_factor", 0) or 0), 2),
                "oos_return": round(float(wf.test_metrics.get("total_return_pct", 0)), 1),
                "oos_trades": int(wf.test_metrics.get("total_trades", 0)),
                "oos_sharpe": round(float(wf.test_metrics.get("sharpe", 0)), 2),
                "score": round(score, 3),
            })
            if score > best_score:
                best_score = score
                best_params = {"reward_risk": rr, "swing_lookback": sl, "signal_mode": mode}
                best_metrics = wf.test_metrics
        except Exception as e:
            pass

        if done % 10 == 0 or done == total:
            print(f"  {done}/{total} kombinationer testade...", flush=True)

    # Sortera och visa topp 5
    results.sort(key=lambda x: x["score"], reverse=True)
    print(f"\n  Topp 5 parameterkombinationer för {symbol}:")
    print(f"  {'RR':>4} {'SL':>4} {'Mode':<18} {'PF':>6} {'Ret%':>7} {'Tr':>4} {'Sh':>6} {'Score':>7}")
    for r in results[:5]:
        print(f"  {r['reward_risk']:>4.1f} {r['swing_lookback']:>4}  {r['signal_mode']:<16} "
              f"{r['oos_pf']:>6.2f} {r['oos_return']:>7.1f} {r['oos_trades']:>4} "
              f"{r['oos_sharpe']:>6.2f} {r['score']:>7.3f}")

    if best_params:
        print(f"\n  ✅ Bäst: RR={best_params['reward_risk']} SL={best_params['swing_lookback']} mode={best_params['signal_mode']}")
        print(f"     OOS PF={round(float(best_metrics.get('profit_factor',0) or 0),2)}  "
              f"Return={round(float(best_metrics.get('total_return_pct',0)),1)}%  "
              f"Sharpe={round(float(best_metrics.get('sharpe',0)),2)}")

    return {
        "symbol": symbol,
        "best_params": best_params,
        "best_score": round(best_score, 3),
        "best_oos_pf": round(float(best_metrics.get("profit_factor", 0) or 0), 2),
        "best_oos_return": round(float(best_metrics.get("total_return_pct", 0)), 1),
        "best_oos_trades": int(best_metrics.get("total_trades", 0)),
        "best_oos_sharpe": round(float(best_metrics.get("sharpe", 0)), 2),
        "all_results": results[:10],
    }


def main() -> int:
    print("STEG 1: PARAMETEROPTIMERING PER SYMBOL")
    print(f"Testar {len(REWARD_RISKS) * len(SWING_LOOKBACKS) * len(SIGNAL_MODES)} kombinationer per symbol")

    output = {}
    for sym in SYMBOLS:
        result = optimize_symbol(sym)
        output[sym] = result

    # Spara
    out_path = ROOT / "optimized_params.json"
    out_path.write_text(json.dumps(output, indent=2))

    print("\n" + "="*55)
    print("SAMMANFATTNING — optimerade parametrar")
    print("="*55)
    print(f"{'Symbol':<12} {'RR':>4} {'SL':>4} {'Mode':<18} {'PF':>6} {'Ret%':>7} {'Sh':>6}")
    print("-"*60)
    for sym, data in output.items():
        p = data["best_params"]
        if p:
            print(f"{sym:<12} {p.get('reward_risk','-'):>4} {p.get('swing_lookback','-'):>4}  "
                  f"{p.get('signal_mode','-'):<16} {data['best_oos_pf']:>6.2f} "
                  f"{data['best_oos_return']:>7.1f} {data['best_oos_sharpe']:>6.2f}")

    print(f"\nSparat till: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
