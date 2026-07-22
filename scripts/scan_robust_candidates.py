#!/usr/bin/env python3
"""
STENHÅRD robusthetsscanner för nya solida par.

Lärdom från 15m-debaclet: ett enda lyckat OOS-fönster bevisar ingenting.
Här krävs att en strategi klarar TVÅ separata osedda perioder:

  Data delas i: [--- TRAIN 50% ---][- OOS_MID 25% -][- OOS_RECENT 25% -]

  KRAV för att godkännas (robust):
    OOS_RECENT (senaste 25%):  PF>=1.3, Sharpe>=0.7, trades>=12, return>0
    OOS_MID    (mitten 25%):   PF>=1.1, return>0   (får ej förlora)
    Dessutom: 70/30-split OOS ska också vara positiv (tredje kontrollen)

En strategi som klarar alla tre är robust över olika marknadsregimer.
Använder full cachad data (crypto) + Yahoo 1d (aktier/ETF).
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

# Befintliga par att undvika (symbol, strategy, tf)
EXISTING = {
    ("AMD","donchian_breakout","1d"),("AVGO","donchian_breakout","1d"),
    ("DIA","rsi2_reversion","1d"),("EEM","rsi2_reversion","1d"),
    ("GLD","rsi2_reversion","1d"),("GOOGL","adaptive_trend_pullback","1d"),
    ("IWM","adaptive_trend_pullback","1d"),("IWM","rsi2_reversion","1d"),
    ("JPM","macd_pullback","1d"),("NVDA","rsi2_reversion","1d"),
    ("QQQ","rsi2_reversion","1d"),("SPY","rsi2_reversion","1d"),
    ("WMT","macd_pullback","1d"),("GC=F","donchian_breakout","1d"),
    ("SI=F","donchian_bidirectional","1d"),
}

# Kandidat-universum — NYA symboler/strategier
# ETF:er (Yahoo 1d)
ETFS = ["XLK","XLF","XLV","XLE","XLY","XLP","XLI","XLU","SMH","XBI",
        "VTI","MDY","EFA","VWO","TLT","HYG","LQD","VNQ","SPY","QQQ","IWM","DIA","GLD","SLV"]
# Aktier (Yahoo 1d)
STOCKS = ["MSFT","AAPL","AMZN","META","COST","V","MA","HD","UNH","JNJ",
          "PG","KO","PEP","MCD","DIS","BA","CAT","XOM","CVX","NVDA","AMD","AVGO","JPM","WMT"]
# Crypto (cachad Binance, 1d)
CRYPTO = ["BTC-USD","ETH-USD","SOL-USD","XRP-USD","ADA-USD","DOT-USD",
          "LTC-USD","AVAX-USD","LINK-USD","DOGE-USD","BNB-USD","ATOM-USD","NEAR-USD"]

# Strategier att testa per tillgångsklass
MEANREV = ["rsi2_reversion"]
TREND   = ["donchian_breakout","adaptive_trend_pullback","macd_pullback"]

TASKS = []
for s in ETFS:
    for strat in MEANREV + TREND:
        TASKS.append((s, strat, "1d", "yahoo"))
for s in STOCKS:
    for strat in MEANREV + TREND:
        TASKS.append((s, strat, "1d", "yahoo"))
for s in CRYPTO:
    for strat in TREND + MEANREV:
        TASKS.append((s, strat, "1d", "crypto"))


def _run_segment(cfg, strat_name, full, lo, hi):
    seg = full.iloc[lo:hi]
    if len(seg) < 40:
        return None
    res = BacktestEngine(cfg).run(seg, STRATEGIES[strat_name](cfg))
    return compute_metrics(res)


def evaluate(sym, strat_name, tf, src) -> dict | None:
    try:
        if (sym, strat_name, tf) in EXISTING:
            return None
        cls = STRATEGIES.get(strat_name)
        if not cls:
            return None

        start = "2012-01-01" if src == "yahoo" else "2019-01-01"
        cfg = BacktestConfig(symbol=sym, timeframe=tf, entry_timeframe=tf, regime_timeframe="1d",
                             reward_risk=2.0, swing_lookback=10,
                             initial_capital=30000, risk_per_trade=0.0075)
        # RSI2 defaults (robusta från tidigare)
        cfg.__dict__["rsi2_oversold"] = 10.0
        cfg.__dict__["rsi2_atr_sl"] = 2.5
        cfg.__dict__["rsi2_exit_sma"] = 5

        entry = fetch_ohlcv(sym, tf, start=start, refresh=False)
        regime = fetch_ohlcv(sym, "1d", start="2010-01-01", refresh=False)
        if len(entry) < 400:
            return None
        ef = prepare_entry_frame(entry, cfg)
        full = apply_regime_to_entry(ef, entry, cfg)
        n = len(full)
        if n < 400:
            return None

        # Tre-vägs split
        i50 = int(n*0.5); i75 = int(n*0.75)
        i70 = int(n*0.7)

        mid = _run_segment(cfg, strat_name, full, i50, i75)
        recent = _run_segment(cfg, strat_name, full, i75, n)
        oos70 = _run_segment(cfg, strat_name, full, i70, n)
        if not (mid and recent and oos70):
            return None

        def g(m,k): return float(m.get(k,0) or 0)

        rec_pf, rec_sh, rec_tr, rec_ret = g(recent,"profit_factor"), g(recent,"sharpe"), int(recent.get("total_trades",0)), g(recent,"total_return_pct")
        mid_pf, mid_ret = g(mid,"profit_factor"), g(mid,"total_return_pct")
        o70_ret, o70_pf = g(oos70,"total_return_pct"), g(oos70,"profit_factor")

        robust = (
            rec_pf >= 1.3 and rec_sh >= 0.7 and rec_tr >= 12 and rec_ret > 0
            and mid_pf >= 1.1 and mid_ret > 0
            and o70_ret > 0 and o70_pf >= 1.2
        )
        return dict(
            symbol=sym, strategy=strat_name, timeframe=tf, source=src,
            robust=robust,
            recent_pf=round(rec_pf,2), recent_sharpe=round(rec_sh,2),
            recent_trades=rec_tr, recent_ret=round(rec_ret,1),
            recent_dd=round(g(recent,"max_drawdown_pct"),1),
            recent_wr=round(g(recent,"win_rate_pct"),0),
            mid_pf=round(mid_pf,2), mid_ret=round(mid_ret,1),
            oos70_pf=round(o70_pf,2), oos70_ret=round(o70_ret,1),
        )
    except Exception:
        return None


def main():
    print("=" * 74)
    print(f"ROBUSTHETSSCANNER — {len(TASKS)} kombinationer, dubbel-OOS-krav")
    print("=" * 74)
    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(evaluate, *t): t for t in TASKS}
        done = 0
        for f in as_completed(futs):
            done += 1
            r = f.result()
            if r:
                results.append(r)
                if r["robust"]:
                    print(f"  ✅ ROBUST  {r['symbol']:<7}{r['strategy']:<24} "
                          f"recent: PF={r['recent_pf']} Sh={r['recent_sharpe']} "
                          f"Tr={r['recent_trades']} Ret={r['recent_ret']}%  "
                          f"mid: PF={r['mid_pf']} Ret={r['mid_ret']}%")
            if done % 40 == 0:
                print(f"  ... {done}/{len(TASKS)}")

    robust = [r for r in results if r["robust"]]
    robust.sort(key=lambda x: x["recent_pf"] * max(x["recent_sharpe"], 0.1), reverse=True)

    print(f"\n{'='*74}")
    print(f"ROBUSTA KANDIDATER: {len(robust)}")
    print(f"  {'Symbol':<7}{'Strategi':<24}{'RecPF':>6}{'RecSh':>6}{'RecRet':>7}{'MidPF':>6}{'O70Ret':>7}")
    print("  " + "-"*62)
    for r in robust:
        print(f"  {r['symbol']:<7}{r['strategy']:<24}{r['recent_pf']:>6.2f}{r['recent_sharpe']:>6.2f}"
              f"{r['recent_ret']:>6.1f}%{r['mid_pf']:>6.2f}{r['oos70_ret']:>6.1f}%")

    out = ROOT / "scan_robust_candidates.json"
    out.write_text(json.dumps({"robust": robust, "all": results}, indent=2))
    print(f"\nSparat: {out}   ({len(robust)} robusta av {len(results)} testade)")


if __name__ == "__main__":
    main()
