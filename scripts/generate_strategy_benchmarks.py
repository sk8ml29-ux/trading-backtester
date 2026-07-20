#!/usr/bin/env python3
"""
Genererar förväntade prestandaprofiler ("benchmarks") för varje strategi/par
från OOS walk-forward. Sparar till strategy_benchmarks.json.

Profilen används av risk/strategy_health.py för att i realtid jämföra
live-resultat mot vad backtesten förutsade — och flagga/pausa strategier
som degraderat.

Varje profil innehåller:
  - expected_monthly_return : snittavkastning/månad (OOS)
  - monthly_std             : normal svängning/månad
  - expected_win_rate       : förväntad win-rate
  - expected_max_dd         : värsta drawdown i OOS
  - trades_per_month        : förväntad trade-frekvens
  - oos_trades, oos_pf, oos_sharpe
"""
from __future__ import annotations
import sys, json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.data_loader import fetch_ohlcv
from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics
from backtest.mtf import apply_regime_to_entry, prepare_entry_frame
from config import BacktestConfig
from strategies import STRATEGIES

# Portföljer att benchmarka (fil, default risk_per_trade som körs live)
PORTFOLIOS = [
    ("mixed_portfolio_oos_meanrev.json", 0.0075),
    ("mixed_portfolio_oos_stocks.json",  0.0075),
    ("mixed_portfolio_oos.json",         0.0075),
]

# Parameter-nycklar som kan finnas per par
PARAM_KEYS = (
    "rsi2_oversold", "rsi2_atr_sl", "rsi2_exit_sma", "rsi2_period",
    "rsi2_trend_sma", "rsi2_max_rr",
    "reward_risk", "swing_lookback", "macd_signal_mode",
)


def _bars_per_month(tf: str) -> float:
    return {"15m": 30*24*4, "30m": 30*24*2, "1h": 30*24, "4h": 30*6, "1d": 21}.get(tf, 21)


def benchmark_pair(sym: str, strat_name: str, tf: str, params: dict,
                   risk: float) -> dict | None:
    try:
        cls = STRATEGIES.get(strat_name)
        if not cls:
            return None

        regime_tf = "1d"
        entry_start = "2012-01-01" if tf == "1d" else ("2021-01-01" if tf in ("15m","30m") else "2020-01-01")
        cfg = BacktestConfig(
            symbol=sym, timeframe=tf, entry_timeframe=tf, regime_timeframe=regime_tf,
            reward_risk=float(params.get("reward_risk", 2.0)),
            swing_lookback=int(params.get("swing_lookback", 16)),
            initial_capital=30000, risk_per_trade=risk,
        )
        for k in PARAM_KEYS:
            if k in params:
                cfg.__dict__[k] = params[k]

        entry_df  = fetch_ohlcv(sym, tf,        start=entry_start,  refresh=False)
        regime_df = fetch_ohlcv(sym, regime_tf, start="2010-01-01", refresh=False)
        if len(entry_df) < 250:
            return None

        ef = prepare_entry_frame(entry_df, cfg)
        full = apply_regime_to_entry(ef, entry_df, cfg)
        split = int(len(full) * 0.7)
        test = full.iloc[split:]
        if len(test) < 50:
            return None

        res = BacktestEngine(cfg).run(test, cls(cfg))
        m = compute_metrics(res)
        if int(m.get("total_trades", 0)) < 5:
            return None

        # Månadsvis avkastning från trades (delat konto, compounding)
        rows = [(pd.Timestamp(t.exit_time), t.pnl_pct) for t in res.trades if t.exit_time]
        if not rows:
            return None
        tdf = pd.DataFrame(rows, columns=["date", "pnlpct"]).set_index("date").sort_index()
        monthly = tdf["pnlpct"].resample("ME").apply(lambda x: float(np.prod(1 + x) - 1))
        monthly = monthly.dropna()

        oos_days = (test.index[-1] - test.index[0]).days
        months = max(oos_days / 30.0, 1.0)

        return {
            "symbol": sym,
            "strategy": strat_name,
            "timeframe": tf,
            "risk_per_trade": risk,
            "expected_monthly_return": round(float(monthly.mean()) if len(monthly) else 0.0, 4),
            "monthly_std": round(float(monthly.std()) if len(monthly) > 1 else 0.0, 4),
            "expected_win_rate": round(float(m.get("win_rate_pct", 0)), 1),
            "expected_max_dd": round(abs(float(m.get("max_drawdown_pct", 0))), 1),
            "trades_per_month": round(int(m.get("total_trades", 0)) / months, 2),
            "oos_trades": int(m.get("total_trades", 0)),
            "oos_pf": round(float(m.get("profit_factor", 0) or 0), 2),
            "oos_sharpe": round(float(m.get("sharpe", 0) or 0), 2),
            "oos_return_pct": round(float(m.get("total_return_pct", 0)), 1),
            "oos_months": round(months, 1),
        }
    except Exception as e:
        print(f"  FEL {sym}/{strat_name}: {e}")
        return None


def main():
    print("=" * 70)
    print("GENERERAR STRATEGI-BENCHMARKS (förväntade profiler från OOS)")
    print("=" * 70)

    benchmarks: dict[str, dict] = {}
    for fn, risk in PORTFOLIOS:
        path = ROOT / fn
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        pairs = data.get("pairs", [])
        print(f"\n{fn} ({len(pairs)} par):")
        for p in pairs:
            sym, strat = p["symbol"], p["strategy"]
            tf = p.get("timeframe", "1d")
            params = {k: p[k] for k in PARAM_KEYS if k in p}
            bm = benchmark_pair(sym, strat, tf, params, risk)
            if bm:
                key = f"{sym}_{strat}_{tf}"
                benchmarks[key] = bm
                print(f"  {key:<38}  +{bm['expected_monthly_return']*100:>5.2f}%/mån "
                      f"±{bm['monthly_std']*100:.1f}%  WR={bm['expected_win_rate']:.0f}%  "
                      f"maxDD={bm['expected_max_dd']:.1f}%  {bm['trades_per_month']:.1f} tr/mån")

    out = ROOT / "strategy_benchmarks.json"
    out.write_text(json.dumps({
        "generated_at": str(pd.Timestamp.now().date()),
        "note": "Förväntade profiler från OOS walk-forward. Används av strategy_health.",
        "benchmarks": benchmarks,
    }, indent=2))
    print(f"\nSparat: {out}  ({len(benchmarks)} profiler)")


if __name__ == "__main__":
    main()
