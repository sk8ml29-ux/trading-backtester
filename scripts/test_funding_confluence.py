#!/usr/bin/env python3
"""
OOS walk-forward test av Funding Rate Confluence (FRC) strategin.
Jämför mot baseline macd_pullback på samma symboler och period.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.data_loader import fetch_ohlcv
from config import BacktestConfig
from research.pipeline import scan_symbol
from research.walk_forward import run_walk_forward
from strategies import STRATEGIES

SYMBOLS   = ["BTC-USD", "ETH-USD", "XRP-USD", "NEAR-USD", "ATOM-USD", "BNB-USD"]
TIMEFRAME = "1h"
START     = "2024-01-01"
ROOT      = Path(__file__).resolve().parent.parent


def run_one(symbol: str, strategy_name: str) -> dict:
    cfg = BacktestConfig(
        symbol=symbol,
        timeframe=TIMEFRAME,
        entry_timeframe=TIMEFRAME,
        regime_timeframe="1d",
    )
    entry_df = fetch_ohlcv(symbol, TIMEFRAME, start=START, refresh=False)
    regime_df = fetch_ohlcv(symbol, "1d",    start="2022-01-01", refresh=False)

    if len(entry_df) < 300:
        return {"symbol": symbol, "strategy": strategy_name, "error": "too few bars"}

    strategy = STRATEGIES[strategy_name](cfg)
    wf = run_walk_forward(entry_df, regime_df, cfg, strategy)
    tm = wf.test_metrics

    return {
        "symbol":       symbol,
        "strategy":     strategy_name,
        "oos_pass":     wf.test_pass,
        "oos_pf":       round(float(tm.get("profit_factor", 0) or 0), 2),
        "oos_return":   round(float(tm.get("total_return_pct", 0)), 1),
        "oos_trades":   int(tm.get("total_trades", 0)),
        "oos_winrate":  round(float(tm.get("win_rate_pct", 0)), 1),
        "oos_sharpe":   round(float(tm.get("sharpe", 0)), 2),
        "oos_mdd":      round(float(tm.get("max_drawdown_pct", 0)), 1),
        "split_date":   wf.split_date,
    }


def main() -> int:
    print("=== FUNDING CONFLUENCE — OOS WALK-FORWARD TEST ===")
    print(f"Symboler: {SYMBOLS}")
    print(f"Timeframe: {TIMEFRAME}  |  Start: {START}\n")

    results_frc      = []
    results_baseline = []

    for sym in SYMBOLS:
        print(f"Testing {sym}...", flush=True)
        frc      = run_one(sym, "funding_confluence")
        baseline = run_one(sym, "macd_pullback")
        results_frc.append(frc)
        results_baseline.append(baseline)

        pf_frc  = frc.get("oos_pf", 0)
        pf_base = baseline.get("oos_pf", 0)
        tr_frc  = frc.get("oos_trades", 0)
        tr_base = baseline.get("oos_trades", 0)
        pass_frc  = "✅" if frc.get("oos_pass") else "❌"
        pass_base = "✅" if baseline.get("oos_pass") else "❌"

        print(f"  FRC:      {pass_frc} PF={pf_frc:.2f}  ret={frc.get('oos_return',0):.1f}%  trades={tr_frc}  sharpe={frc.get('oos_sharpe',0):.2f}")
        print(f"  Baseline: {pass_base} PF={pf_base:.2f}  ret={baseline.get('oos_return',0):.1f}%  trades={tr_base}  sharpe={baseline.get('oos_sharpe',0):.2f}")
        print()

    # Sammanfattning
    print("=" * 65)
    print(f"{'Symbol':<12} {'FRC PF':>7} {'Base PF':>8} {'FRC Ret%':>9} {'Base Ret%':>10} {'FRC Trades':>11} {'Base Trades':>12}")
    print("-" * 65)
    for frc, base in zip(results_frc, results_baseline):
        sym = frc["symbol"]
        print(
            f"  {sym:<10} {frc.get('oos_pf',0):>7.2f} {base.get('oos_pf',0):>8.2f} "
            f"{frc.get('oos_return',0):>9.1f} {base.get('oos_return',0):>10.1f} "
            f"{frc.get('oos_trades',0):>11} {base.get('oos_trades',0):>12}"
        )

    # Genomsnitt
    frc_passed  = [r for r in results_frc if r.get("oos_pass")]
    base_passed = [r for r in results_baseline if r.get("oos_pass")]
    avg_pf_frc  = sum(r["oos_pf"] for r in frc_passed)  / max(len(frc_passed), 1)
    avg_pf_base = sum(r["oos_pf"] for r in base_passed) / max(len(base_passed), 1)

    print("-" * 65)
    print(f"\nOOS godkända — FRC: {len(frc_passed)}/{len(SYMBOLS)}  |  Baseline: {len(base_passed)}/{len(SYMBOLS)}")
    print(f"Genomsnittligt PF  — FRC: {avg_pf_frc:.2f}  |  Baseline: {avg_pf_base:.2f}")

    # Spara resultat
    output = {
        "strategy": "funding_confluence",
        "timeframe": TIMEFRAME,
        "symbols": SYMBOLS,
        "frc_results": results_frc,
        "baseline_results": results_baseline,
    }
    out_path = ROOT / "funding_confluence_oos.json"
    out_path.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nResultat sparade: {out_path}")

    if len(frc_passed) >= 3 and avg_pf_frc >= 1.4:
        print("\n✅ GODKÄND — Lägg till funding_confluence i live-portfoljen")
    else:
        print("\n⚠️  Ej tillräcklig OOS-validering för live-deploy")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
