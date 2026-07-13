#!/usr/bin/env python3
"""
Steg 2+3: OOS-test av MACD Bidirectional (MBD) strategi.
Testar på BTC och ETH som misslyckades med long-only MACD.
Jämför med baseline (squeeze_bidirectional som redan används på dessa).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.data_loader import fetch_ohlcv
from config import BacktestConfig
from research.walk_forward import run_walk_forward
from strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent.parent

# BTC och ETH klarade inte long-only. Testar nu bidir.
SYMBOLS   = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"]
TIMEFRAME = "1h"
START     = "2024-01-01"


def run_one(symbol: str, strategy_name: str, reward_risk: float = 2.0,
            swing_lookback: int = 16, signal_mode: str = "either") -> dict:
    cfg = BacktestConfig(
        symbol=symbol,
        timeframe=TIMEFRAME,
        entry_timeframe=TIMEFRAME,
        regime_timeframe="1d",
        reward_risk=reward_risk,
        swing_lookback=swing_lookback,
        macd_signal_mode=signal_mode,
    )
    entry_df  = fetch_ohlcv(symbol, TIMEFRAME, start=START, refresh=False)
    regime_df = fetch_ohlcv(symbol, "1d", start="2022-01-01", refresh=False)

    strategy = STRATEGIES[strategy_name](cfg)
    wf = run_walk_forward(entry_df, regime_df, cfg, strategy)
    tm = wf.test_metrics

    return {
        "symbol":      symbol,
        "strategy":    strategy_name,
        "oos_pass":    wf.test_pass,
        "oos_pf":      round(float(tm.get("profit_factor", 0) or 0), 2),
        "oos_return":  round(float(tm.get("total_return_pct", 0)), 1),
        "oos_trades":  int(tm.get("total_trades", 0)),
        "oos_winrate": round(float(tm.get("win_rate_pct", 0)), 1),
        "oos_sharpe":  round(float(tm.get("sharpe", 0)), 2),
        "oos_mdd":     round(float(tm.get("max_drawdown_pct", 0)), 1),
        "split_date":  wf.split_date,
    }


def main() -> int:
    print("=== STEG 2+3: MACD BIDIRECTIONAL — OOS TEST ===")
    print(f"Symboler: {SYMBOLS}")
    print(f"OOS-period: okt 2025 – jul 2026\n")

    results_mbd  = []
    results_base = []

    for sym in SYMBOLS:
        print(f"Testar {sym}...", flush=True)
        mbd  = run_one(sym, "macd_bidirectional", reward_risk=2.0, swing_lookback=12, signal_mode="either")
        base = run_one(sym, "squeeze_bidirectional")  # befintlig strategi på BTC/ETH
        results_mbd.append(mbd)
        results_base.append(base)

        p_mbd  = "✅" if mbd["oos_pass"]  else "❌"
        p_base = "✅" if base["oos_pass"] else "❌"
        print(f"  MBD:  {p_mbd} PF={mbd['oos_pf']:.2f}  ret={mbd['oos_return']:.1f}%  "
              f"trades={mbd['oos_trades']}  sharpe={mbd['oos_sharpe']:.2f}  mdd={mbd['oos_mdd']:.1f}%")
        print(f"  BASE: {p_base} PF={base['oos_pf']:.2f}  ret={base['oos_return']:.1f}%  "
              f"trades={base['oos_trades']}  sharpe={base['oos_sharpe']:.2f}  mdd={base['oos_mdd']:.1f}%")
        print()

    print("=" * 68)
    print(f"{'Symbol':<12} {'MBD PF':>7} {'Base PF':>8} {'MBD Ret%':>9} {'Base Ret%':>10} {'MBD Tr':>7} {'Base Tr':>8}")
    print("-" * 68)
    for m, b in zip(results_mbd, results_base):
        print(f"  {m['symbol']:<10} {m['oos_pf']:>7.2f} {b['oos_pf']:>8.2f} "
              f"{m['oos_return']:>9.1f} {b['oos_return']:>10.1f} "
              f"{m['oos_trades']:>7} {b['oos_trades']:>8}")

    mbd_pass  = [r for r in results_mbd  if r["oos_pass"]]
    base_pass = [r for r in results_base if r["oos_pass"]]
    avg_mbd   = sum(r["oos_pf"] for r in mbd_pass)  / max(len(mbd_pass),  1)
    avg_base  = sum(r["oos_pf"] for r in base_pass) / max(len(base_pass), 1)

    print("-" * 68)
    print(f"\nOOS godkända — MBD: {len(mbd_pass)}/{len(SYMBOLS)}  |  Baseline: {len(base_pass)}/{len(SYMBOLS)}")
    print(f"Genomsnittligt PF  — MBD: {avg_mbd:.2f}  |  Baseline: {avg_base:.2f}")

    # Spara
    output = {
        "strategy": "macd_bidirectional",
        "mbd_results": results_mbd,
        "baseline_results": results_base,
    }
    out = ROOT / "mbd_oos.json"
    out.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nResultat sparade: {out}")

    if len(mbd_pass) >= 2 and avg_mbd >= 1.3:
        print("\n✅ MBD godkänd för BTC/ETH — ersätter squeeze_bidirectional")
    else:
        print("\n⚠️  MBD ej bättre — behåll squeeze_bidirectional")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
