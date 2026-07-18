#!/usr/bin/env python3
"""
OOS walk-forward test av MicroOrbStrategy på 15m.
Testar olika session-längder och ORB-perioder.
15m ger 730 dagars historik = tillräckligt för OOS.
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
from strategies.micro_orb import MicroOrbStrategy

# Bäst-lämpade symboler för ORB (volatila, trendiga)
SYMBOLS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD",   # Crypto
    "SPY", "QQQ", "IWM",                            # ETF:er
    "AMD", "NVDA", "TSLA", "AAPL",                  # Aktier
    "GC=F", "SI=F",                                  # Råvaror
]

# Grid: session-längd och ORB-period
CONFIGS = [
    # (orb_period, session_bars, rr, label)
    (4,  24, 2.0, "ORB 1h-range, 6h-session, RR2"),  # Default
    (2,  24, 2.0, "ORB 30m-range, 6h-session, RR2"),
    (4,  48, 2.0, "ORB 1h-range, 12h-session, RR2"),
    (4,  24, 1.5, "ORB 1h-range, 6h-session, RR1.5"),
    (8,  48, 2.0, "ORB 2h-range, 12h-session, RR2"),
    (4,  24, 3.0, "ORB 1h-range, 6h-session, RR3"),
]

TF = "15m"
START = "2024-01-01"
REGIME_START = "2023-01-01"


def run_one(sym: str, orb_period: int, session_bars: int, rr: float, label: str) -> dict | None:
    try:
        cfg = BacktestConfig(
            symbol=sym, timeframe=TF, entry_timeframe=TF, regime_timeframe="1d",
            reward_risk=rr, swing_lookback=orb_period,
        )
        # Patch ORB-params direkt på config
        cfg.__dict__["orb_period"]      = orb_period
        cfg.__dict__["orb_rr"]          = rr
        cfg.__dict__["orb_session_bars"] = session_bars

        entry_df  = fetch_ohlcv(sym, TF,  start=START,        refresh=False)
        regime_df = fetch_ohlcv(sym, "1d", start=REGIME_START, refresh=False)

        if len(entry_df) < 500:
            return None

        strategy = MicroOrbStrategy(cfg)
        wf = run_walk_forward(entry_df, regime_df, cfg, strategy)
        m  = wf.test_metrics

        return dict(
            symbol=sym, config=label, timeframe=TF,
            orb_period=orb_period, session_bars=session_bars, rr=rr,
            oos_pass=wf.test_pass,
            oos_pf=round(float(m.get("profit_factor", 0) or 0), 2),
            oos_return=round(float(m.get("total_return_pct", 0) or 0), 1),
            oos_trades=int(m.get("total_trades", 0) or 0),
            oos_sharpe=round(float(m.get("sharpe", 0) or 0), 2),
            split_date=wf.split_date,
        )
    except Exception as e:
        print(f"  FEL {sym}/{label}: {e}")
        return None


def main():
    print("=" * 72)
    print("MICRO ORB — OOS WALK-FORWARD PÅ 15m (730 dagars historik)")
    print("=" * 72)

    tasks = [(sym, *c) for sym in SYMBOLS for c in CONFIGS]
    results = []

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(run_one, *t): t for t in tasks}
        done = 0
        for fut in as_completed(futures):
            done += 1
            r = fut.result()
            if r:
                results.append(r)
                if r["oos_pass"]:
                    print(f"  ✅ {done:>3}/{len(tasks)}  PF={r['oos_pf']:>5.2f}  "
                          f"Sh={r['oos_sharpe']:>5.2f}  Tr={r['oos_trades']:>3}  "
                          f"{r['symbol']:<10}  {r['config']}")
            if done % 20 == 0:
                print(f"  ... {done}/{len(tasks)} klara")

    strong = [r for r in results if r["oos_pass"] and r["oos_pf"] >= 1.5]
    good   = [r for r in results if r["oos_pass"] and 1.1 <= r["oos_pf"] < 1.5]
    strong.sort(key=lambda x: x["oos_pf"], reverse=True)
    good.sort(key=lambda x: x["oos_pf"],   reverse=True)

    print(f"\n{'STARKA OOS (PF >= 1.5)'}")
    print(f"  {'Symbol':<10}  {'Config':<36}  {'PF':>5}  {'Ret%':>6}  {'Tr':>4}  {'Sh':>5}")
    print("  " + "-" * 70)
    if strong:
        for r in strong:
            print(f"  {r['symbol']:<10}  {r['config']:<36}  {r['oos_pf']:>5.2f}  "
                  f"{r['oos_return']:>6.1f}  {r['oos_trades']:>4}  {r['oos_sharpe']:>5.2f}  ✅")
    else:
        print("  Inga starka OOS-kandidater.")

    if good:
        print(f"\n{'GODKÄNDA (PF 1.1-1.5)'}")
        for r in good[:8]:
            print(f"  {r['symbol']:<10}  {r['config']:<36}  {r['oos_pf']:>5.2f}  "
                  f"{r['oos_return']:>6.1f}  {r['oos_trades']:>4}  {r['oos_sharpe']:>5.2f}")

    # Sammanfatta bästa config per symbol
    best_per_sym: dict[str, dict] = {}
    for r in results:
        if r["oos_pass"]:
            sym = r["symbol"]
            if sym not in best_per_sym or r["oos_pf"] > best_per_sym[sym]["oos_pf"]:
                best_per_sym[sym] = r

    out = ROOT / "scan_micro_orb_15m.json"
    out.write_text(json.dumps({
        "strategy": "micro_orb",
        "timeframe": TF,
        "scanned_at": str(__import__("datetime").date.today()),
        "strong": strong,
        "good": good,
        "best_per_symbol": best_per_sym,
        "all": results,
    }, indent=2))
    print(f"\nResultat sparade: {out}")
    print(f"Sammanfattning: {len(strong)} starka + {len(good)} godkända av {len(results)} testade.")


if __name__ == "__main__":
    main()
