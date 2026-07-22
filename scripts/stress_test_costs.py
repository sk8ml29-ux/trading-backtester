#!/usr/bin/env python3
"""
Kostnadsstresstest — kör varje portfölj-par vid 1x, 2x och 3x kostnader.
Avslöjar vilka strategier som tål verklig/högre spread och vilka som vippar
till förlust. En robust strategi ska överleva 2-3x modellerade kostnader.
"""
from __future__ import annotations
import sys, json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.data_loader import fetch_ohlcv
from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics
from backtest.mtf import apply_regime_to_entry, prepare_entry_frame
from config import BacktestConfig
from strategies import STRATEGIES

PORTFOLIOS = [
    "mixed_portfolio_oos_meanrev.json",
    "mixed_portfolio_oos_stocks.json",
    "mixed_portfolio_oos_spicy.json",
]

PARAM_KEYS = ("rsi2_oversold","rsi2_atr_sl","rsi2_exit_sma","reward_risk","swing_lookback",
              "conviction_high_checks","conviction_risk_mult")


def run_pair(sym, strat, tf, params, cost_mult) -> dict | None:
    try:
        cls = STRATEGIES.get(strat)
        if not cls:
            return None
        cfg = BacktestConfig(symbol=sym, timeframe=tf, entry_timeframe=tf, regime_timeframe="1d",
                             reward_risk=float(params.get("reward_risk",2.0)),
                             swing_lookback=int(params.get("swing_lookback",10)),
                             initial_capital=30000, risk_per_trade=0.0075)
        # Skala baskostnader
        cfg.commission_pct = 0.0005 * cost_mult
        cfg.slippage_pct = 0.0003 * cost_mult
        cfg.spread_pct = 0.0002 * cost_mult
        for k in PARAM_KEYS:
            if k in params:
                cfg.__dict__[k] = params[k]
        start = "2012-01-01" if not sym.endswith("-USD") else "2019-01-01"
        entry = fetch_ohlcv(sym, tf, start=start, refresh=False)
        if len(entry) < 400:
            return None
        ef = prepare_entry_frame(entry, cfg)
        full = apply_regime_to_entry(ef, entry, cfg)
        test = full.iloc[int(len(full)*0.7):]
        res = BacktestEngine(cfg).run(test, cls(cfg))
        m = compute_metrics(res)
        return dict(pf=round(float(m.get("profit_factor",0) or 0),2),
                    ret=round(float(m.get("total_return_pct",0)),1),
                    tr=int(m.get("total_trades",0)))
    except Exception:
        return None


def main():
    print("=" * 78)
    print("KOSTNADSSTRESSTEST — 1x / 2x / 3x modellerade kostnader (70/30 OOS)")
    print("=" * 78)
    print(f"{'Symbol':<8}{'Strategi':<20}{'1x PF':>7}{'1x Ret':>8}{'2x PF':>7}{'2x Ret':>8}{'3x PF':>7}{'3x Ret':>8}  Dom")
    print("-" * 78)

    pairs = []
    for fn in PORTFOLIOS:
        p = ROOT / fn
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        for pr in d.get("pairs", []):
            if pr.get("disabled"):
                continue
            params = {k: pr[k] for k in PARAM_KEYS if k in pr}
            pairs.append((pr["symbol"], pr["strategy"], pr.get("timeframe","1d"), params))

    survivors = weak = 0
    for sym, strat, tf, params in pairs:
        r1 = run_pair(sym, strat, tf, params, 1.0)
        r2 = run_pair(sym, strat, tf, params, 2.0)
        r3 = run_pair(sym, strat, tf, params, 3.0)
        if not (r1 and r2 and r3):
            print(f"{sym:<8}{strat:<20}  (data saknas)")
            continue
        # Dom: robust om PF>=1.3 och positiv även vid 3x
        verdict = "✅ TÅL 3x" if (r3["pf"] >= 1.3 and r3["ret"] > 0) else (
                  "🟡 TÅL 2x" if (r2["pf"] >= 1.2 and r2["ret"] > 0) else "🔴 KÄNSLIG")
        if "✅" in verdict: survivors += 1
        elif "🔴" in verdict: weak += 1
        print(f"{sym:<8}{strat:<20}{r1['pf']:>7.2f}{r1['ret']:>7.1f}%{r2['pf']:>7.2f}{r2['ret']:>7.1f}%"
              f"{r3['pf']:>7.2f}{r3['ret']:>7.1f}%  {verdict}")

    print("-" * 78)
    print(f"✅ Tål 3x kostnader: {survivors}   🔴 Känsliga: {weak}   (av {len(pairs)} par)")


if __name__ == "__main__":
    main()
