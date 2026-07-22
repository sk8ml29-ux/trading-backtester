#!/usr/bin/env python3
"""
Scannar ConvictionStack ("kryddan") över aktier/ETF/crypto.
Använder samma stränga dubbel-OOS-krav som robusthetsscanner + rapporterar
hur hög-conviction-satsningarna presterar.
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

# Volatila, trendiga tillgångar passar conviction-momentum bäst
UNIVERSE = [
    ("NVDA","yahoo"),("AMD","yahoo"),("TSLA","yahoo"),("META","yahoo"),
    ("AVGO","yahoo"),("SMH","yahoo"),("AMZN","yahoo"),("AAPL","yahoo"),
    ("MSFT","yahoo"),("GOOGL","yahoo"),("NFLX","yahoo"),("CAT","yahoo"),
    ("QQQ","yahoo"),("XLK","yahoo"),("XBI","yahoo"),
    ("BTC-USD","crypto"),("ETH-USD","crypto"),("SOL-USD","crypto"),
]
RR_LIST = [2.0, 2.5, 3.0]


def _seg(cfg, full, lo, hi):
    seg = full.iloc[lo:hi]
    if len(seg) < 40:
        return None, None
    res = BacktestEngine(cfg).run(seg, STRATEGIES["conviction_stack"](cfg))
    return compute_metrics(res), res


def evaluate(sym, src, rr) -> dict | None:
    try:
        start = "2013-01-01" if src == "yahoo" else "2019-01-01"
        cfg = BacktestConfig(symbol=sym, timeframe="1d", entry_timeframe="1d", regime_timeframe="1d",
                             reward_risk=rr, swing_lookback=10, initial_capital=30000, risk_per_trade=0.0075)
        entry = fetch_ohlcv(sym, "1d", start=start, refresh=False)
        regime = fetch_ohlcv(sym, "1d", start="2010-01-01", refresh=False)
        if len(entry) < 400:
            return None
        ef = prepare_entry_frame(entry, cfg)
        full = apply_regime_to_entry(ef, entry, cfg)
        n = len(full)
        i50, i75, i70 = int(n*0.5), int(n*0.75), int(n*0.7)

        mid, _ = _seg(cfg, full, i50, i75)
        recent, recent_res = _seg(cfg, full, i75, n)
        oos70, _ = _seg(cfg, full, i70, n)
        if not (mid and recent and oos70):
            return None

        def g(m,k): return float(m.get(k,0) or 0)
        rec_pf, rec_sh, rec_tr, rec_ret = g(recent,"profit_factor"), g(recent,"sharpe"), int(recent.get("total_trades",0)), g(recent,"total_return_pct")
        hc = sum(1 for t in recent_res.trades if "HÖG" in t.reason)

        # Krydda tillåts något högre DD (aggressiv), men måste vara robust
        robust = (rec_pf >= 1.4 and rec_sh >= 0.6 and rec_tr >= 10 and rec_ret > 0
                  and g(mid,"profit_factor") >= 1.1 and g(mid,"total_return_pct") > 0
                  and g(oos70,"total_return_pct") > 0)
        return dict(symbol=sym, rr=rr, robust=robust,
                    recent_pf=round(rec_pf,2), recent_sharpe=round(rec_sh,2),
                    recent_trades=rec_tr, recent_hc=hc, recent_ret=round(rec_ret,1),
                    recent_dd=round(g(recent,"max_drawdown_pct"),1),
                    mid_pf=round(g(mid,"profit_factor"),2),
                    oos70_ret=round(g(oos70,"total_return_pct"),1))
    except Exception:
        return None


def main():
    tasks = [(s, src, rr) for (s, src) in UNIVERSE for rr in RR_LIST]
    print(f"CONVICTION STACK-SCAN — {len(tasks)} kombinationer")
    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(evaluate, *t): t for t in tasks}
        for f in as_completed(futs):
            r = f.result()
            if r:
                results.append(r)

    robust = [r for r in results if r["robust"]]
    robust.sort(key=lambda x: x["recent_pf"]*max(x["recent_sharpe"],0.1), reverse=True)
    # Bästa RR per symbol
    best = {}
    for r in robust:
        if r["symbol"] not in best or r["recent_pf"] > best[r["symbol"]]["recent_pf"]:
            best[r["symbol"]] = r

    print(f"\nROBUSTA (klarade 3 OOS-fönster): {len(robust)}  |  unika symboler: {len(best)}")
    print(f"  {'Symbol':<9}{'RR':>4}{'RecPF':>7}{'Sh':>6}{'Tr':>4}{'HC':>4}{'Ret%':>7}{'DD%':>6}{'MidPF':>6}")
    print("  " + "-"*54)
    for sym, r in sorted(best.items(), key=lambda x: x[1]["recent_pf"], reverse=True):
        print(f"  {sym:<9}{r['rr']:>4.1f}{r['recent_pf']:>7.2f}{r['recent_sharpe']:>6.2f}"
              f"{r['recent_trades']:>4}{r['recent_hc']:>4}{r['recent_ret']:>6.1f}%{r['recent_dd']:>5.1f}%{r['mid_pf']:>6.2f}")

    out = ROOT / "scan_conviction_stack.json"
    out.write_text(json.dumps({"robust": robust, "best_per_symbol": best, "all": results}, indent=2))
    print(f"\nSparat: {out}")


if __name__ == "__main__":
    main()
