#!/usr/bin/env python3
"""
OOS walk-forward scan på de starkaste in-sample kandidaterna från universe_scan_1d.
Testar råvaror, aktier, crypto och index på 1d timeframe.
Sparar starka OOS-passerade par (PF >= 1.1, Sharpe >= 0.5, Trades >= 15).
"""
from __future__ import annotations
import sys, json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.data_loader import fetch_ohlcv
from config import BacktestConfig
from research.walk_forward import run_walk_forward
from strategies import STRATEGIES

# Top candidates from universe_scan_1d.json (PF >= 1.5, Trades >= 20)
CANDIDATES = [
    ("BTC-USD",  "donchian_breakout"),
    ("AMD",      "squeeze_breakout"),
    ("NVDA",     "kinetic_equilibrium"),
    ("NVDA",     "donchian_breakout"),
    ("AAPL",     "squeeze_bidirectional"),
    ("GOOGL",    "kinetic_equilibrium"),
    ("AMD",      "squeeze_bidirectional"),
    ("SPY",      "squeeze_bidirectional"),
    ("MSFT",     "adaptive_trend_pullback"),
    ("AMD",      "donchian_breakout"),
    ("COST",     "donchian_breakout"),
    ("SOL-USD",  "squeeze_bidirectional"),
    ("MSFT",     "kinetic_equilibrium"),
    ("GOOGL",    "adaptive_trend_pullback"),
    ("AMZN",     "squeeze_breakout"),
    ("GC=F",     "squeeze_breakout"),
    ("AAPL",     "donchian_breakout"),
    ("SPY",      "adaptive_trend_pullback"),
    ("ETH-USD",  "adaptive_trend_pullback"),
    ("NVDA",     "squeeze_breakout"),
    ("NVDA",     "squeeze_bidirectional"),
    ("AMD",      "donchian_bidirectional"),
    ("BTC-USD",  "donchian_bidirectional"),
    ("ETH-USD",  "donchian_breakout"),
    ("AMZN",     "squeeze_bidirectional"),
    ("NVDA",     "macd_pullback"),
    ("AAPL",     "macd_pullback"),
    # Extra: raw materials och silver ETF
    ("GC=F",     "donchian_breakout"),
    ("SLV",      "donchian_breakout"),
    ("SLV",      "squeeze_bidirectional"),
    ("HG=F",     "donchian_breakout"),
    ("CL=F",     "donchian_breakout"),
    # More 1h strong from earlier scan
    ("SLV",      "adaptive_trend_pullback"),
    ("GOOGL",    "squeeze_bidirectional"),
    ("MSFT",     "squeeze_bidirectional"),
    ("AMZN",     "adaptive_trend_pullback"),
]

TF = "1d"
START_ENTRY  = "2018-01-01"
START_REGIME = "2012-01-01"


def _load_strategy(name: str, cfg: BacktestConfig):
    """Load strategy by name from STRATEGIES dict."""
    cls = STRATEGIES.get(name)
    if cls:
        return cls(cfg)
    raise KeyError(f"Strategy not found in STRATEGIES: {name}")


def run_one(sym: str, strat_name: str) -> dict | None:
    try:
        cfg = BacktestConfig(
            symbol=sym,
            timeframe=TF,
            entry_timeframe=TF,
            regime_timeframe=TF,
            reward_risk=2.0,
            swing_lookback=10,
        )
        entry_df  = fetch_ohlcv(sym, TF, start=START_ENTRY,  refresh=False)
        regime_df = fetch_ohlcv(sym, TF, start=START_REGIME, refresh=False)
        if len(entry_df) < 150:
            return None

        strategy = _load_strategy(strat_name, cfg)
        # Daily strategies trade infrequently — 50/50 split and min 10 trades
        wf = run_walk_forward(entry_df, regime_df, cfg, strategy,
                              train_ratio=0.5, min_test_trades=10)
        m  = wf.test_metrics

        pf     = float(m.get("profit_factor", 0) or 0)
        ret    = float(m.get("total_return_pct", 0) or 0)
        trades = int(m.get("total_trades", 0) or 0)
        sharpe = float(m.get("sharpe", 0) or 0)

        return dict(
            symbol=sym, strategy=strat_name, timeframe=TF,
            oos_pass=wf.test_pass,
            oos_pf=round(pf, 2),
            oos_return=round(ret, 1),
            oos_trades=trades,
            oos_sharpe=round(sharpe, 2),
            split_date=wf.split_date,
        )
    except Exception as e:
        print(f"  FEL {sym}/{strat_name}: {e}")
        return None


def main():
    print("=" * 70)
    print("OOS WALK-FORWARD — 1d timeframe, starka in-sample kandidater")
    print("=" * 70)

    results = []
    deduped = list(dict.fromkeys(CANDIDATES))  # remove duplicates preserving order
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(run_one, sym, strat): (sym, strat) for sym, strat in deduped}
        done = 0
        for fut in as_completed(futures):
            done += 1
            r = fut.result()
            sym, strat = futures[fut]
            if r:
                results.append(r)
                status = "✅" if r["oos_pass"] else "❌"
                print(f"  {done:>2}/{len(deduped)}  {status}  PF={r['oos_pf']:>5.2f}  Sh={r['oos_sharpe']:>5.2f}"
                      f"  Tr={r['oos_trades']:>3}  {sym:<10}  {strat}")
            else:
                print(f"  {done:>2}/{len(deduped)}  --  {sym:<10}  {strat}")

    strong     = [r for r in results if r["oos_pass"] and r["oos_pf"] >= 1.5]
    good       = [r for r in results if r["oos_pass"] and 1.1 <= r["oos_pf"] < 1.5]

    strong.sort(key=lambda x: x["oos_pf"], reverse=True)
    good.sort(key=lambda x: x["oos_pf"], reverse=True)

    print(f"\n{'STARKA (PF >= 1.5, OOS validerade)'}")
    print(f"  {'Symbol':<10}  {'Strategi':<25}  {'PF':>5}  {'Ret%':>6}  {'Tr':>4}  {'Sh':>5}")
    print("  " + "-" * 62)
    if strong:
        for r in strong:
            print(f"  {r['symbol']:<10}  {r['strategy']:<25}  {r['oos_pf']:>5.2f}  "
                  f"{r['oos_return']:>6.1f}  {r['oos_trades']:>4}  {r['oos_sharpe']:>5.2f}  ✅")
    else:
        print("  Inga starka OOS-kandidater hittades.")

    if good:
        print(f"\n{'GODKÄNDA (PF 1.1-1.5)'}")
        for r in good:
            print(f"  {r['symbol']:<10}  {r['strategy']:<25}  {r['oos_pf']:>5.2f}  "
                  f"{r['oos_return']:>6.1f}  {r['oos_trades']:>4}  {r['oos_sharpe']:>5.2f}")

    out = ROOT / "scan_strong_oos_1d.json"
    out.write_text(json.dumps({
        "scanned_at": str(__import__("datetime").date.today()),
        "timeframe": TF,
        "strong": strong,
        "good": good,
        "all": [r for r in results if r is not None],
    }, indent=2))
    print(f"\nResultat sparade: {out}")
    print(f"Sammanfattning: {len(strong)} starka + {len(good)} godkända av {len(results)} testade.")


if __name__ == "__main__":
    main()
