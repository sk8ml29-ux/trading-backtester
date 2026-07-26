"""
legal_trend.py — LEGAL long/flat trend-following for a Swedish retail investor.

No perpetuals, no shorting, no leverage, no borrowing. You are either LONG the
asset (spot / ETP / ETF, all tradeable via e.g. Avanza/Nordnet or a regulated
spot exchange) or in CASH. That makes it fully accessible under EU/MiCA/MiFID.

Edge: time-series momentum / trend-following. Hold an asset only while it is in
an uptrend (close above a moving average); step aside to cash otherwise. This
keeps most of the upside while cutting the deep drawdowns of buy-and-hold — a
well-documented, robust effect (especially strong in crypto).

Validated with out-of-sample walk-forward and realistic costs. Honest: this is
NOT the funding-harvest Sharpe; it is the best *legal* edge for EU retail.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"

# Legal, retail-accessible universes (spot / ETP / ETF).
CRYPTO = {  # tradeable as spot or as crypto ETPs on regulated venues
    "BTC": "binance_btc_usd_1d",
    "ETH": "binance_eth_usd_1d",
    "SOL": "binance_sol_usd_1d",
}
ETF = {  # tradeable via any Swedish broker (Avanza/Nordnet)
    "SPY": "spy_1d", "QQQ": "qqq_1d", "IWM": "iwm_1d",
    "EEM": "eem_1d", "GLD": "gld_1d", "SLV": "slv_1d", "TLT": "tlt_1d",
}


def load_close(fname: str) -> pd.Series:
    df = pd.read_csv(CACHE / f"{fname}.csv", parse_dates=["datetime"], index_col="datetime")
    s = df["close"].copy()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s[~s.index.duplicated(keep="last")].sort_index()


def trend_long_flat(close: pd.Series, lookback: int, cost: float,
                    confirm: int = 1) -> pd.Series:
    """Daily strategy returns: long when close>EMA(lookback) (confirmed `confirm`
    days), else flat. Signal uses only past data; cost charged on switches."""
    ema = close.ewm(span=lookback, adjust=False).mean()
    raw = (close > ema)
    if confirm > 1:
        raw = raw.rolling(confirm).apply(lambda x: 1.0 if x.all() else 0.0).fillna(0) > 0
    pos = raw.shift(1).fillna(False).astype(float)      # act next day (no look-ahead)
    ret = close.pct_change().fillna(0.0)
    switch = pos.diff().abs().fillna(0.0)
    strat = pos * ret - switch * cost
    return strat


def buy_hold(close: pd.Series) -> pd.Series:
    return close.pct_change().fillna(0.0)


def metrics(daily: pd.Series, ppy: int) -> dict:
    dr = daily.dropna()
    if len(dr) < 30:
        return {}
    eq = (1 + dr).cumprod()
    yrs = len(dr) / ppy
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    vol = dr.std(ddof=0) * math.sqrt(ppy)
    sharpe = (dr.mean() / dr.std(ddof=0) * math.sqrt(ppy)) if dr.std(ddof=0) > 0 else 0.0
    dd = ((eq - eq.cummax()) / eq.cummax()).min()
    downside = dr[dr < 0].std(ddof=0)
    sortino = (dr.mean() / downside * math.sqrt(ppy)) if downside > 0 else 0.0
    return dict(
        cagr_pct=round(cagr * 100, 2), vol_pct=round(vol * 100, 2),
        sharpe=round(sharpe, 3), sortino=round(sortino, 3),
        max_dd_pct=round(dd * 100, 2),
        pct_in_market=round((dr != 0).mean() * 100, 1),
        total_return_pct=round((eq.iloc[-1] - 1) * 100, 1),
        final_mult=round(float(eq.iloc[-1]), 2),
    )


def portfolio(strats: dict) -> pd.Series:
    """Equal-weight, daily-rebalanced across per-asset long/flat strategies."""
    df = pd.DataFrame(strats).dropna(how="all").fillna(0.0)
    return df.mean(axis=1)


def vol_weighted_trend(closes: dict, lookback: int, cost: float,
                       vol_window: int = 30, target_vol_daily: float = 0.01) -> pd.Series:
    """LEGAL diversified trend book: long/flat per asset, inverse-vol weighted,
    total exposure capped at 1 (no leverage, no shorting). Cash earns 0.

    Diversification across many uncorrelated trends + risk weighting is the main
    lever to lift Sharpe and cut drawdown without leverage."""
    rets, poss, invvol = {}, {}, {}
    for a, s in closes.items():
        ema = s.ewm(span=lookback, adjust=False).mean()
        pos = (s > ema).shift(1).fillna(False).astype(float)
        r = s.pct_change().fillna(0.0)
        v = r.rolling(vol_window).std(ddof=0).shift(1)
        rets[a] = r; poss[a] = pos; invvol[a] = 1.0 / v.replace(0, np.nan)
    R = pd.DataFrame(rets).sort_index()
    P = pd.DataFrame(poss).reindex(R.index).fillna(0.0)
    IV = pd.DataFrame(invvol).reindex(R.index)
    raw = (P * IV).fillna(0.0)                       # risk weight only where long
    denom = IV.where(P > 0).sum(axis=1)              # normalize by long inverse-vols
    W = raw.div(denom.replace(0, np.nan), axis=0).fillna(0.0)
    # scale whole book toward a modest daily-vol target, but never lever above 1
    port_vol = (W.shift(1) * R).sum(axis=1).rolling(vol_window).std(ddof=0).shift(1)
    scale = (target_vol_daily / port_vol).clip(upper=1.0).fillna(1.0)
    W = W.mul(scale, axis=0)
    Wprev = W.shift(1).fillna(0.0)
    gross = (Wprev * R).sum(axis=1)
    turnover = (W - Wprev).abs().sum(axis=1)
    return gross - turnover * cost


def walk_forward(closes: dict, cost: float, ppy: int, lookbacks: list,
                 n_folds: int = 4):
    """Anchored WF: pick the lookback with best in-sample Sharpe (portfolio),
    apply to next OOS block; concatenate OOS."""
    idx = None
    for s in closes.values():
        idx = s.index if idx is None else idx.union(s.index)
    idx = idx.sort_values()
    block = len(idx) // (n_folds + 1)
    oos = []
    picks = []
    for k in range(n_folds):
        tr_hi = idx[(k + 1) * block - 1]
        te_hi = idx[min((k + 2) * block - 1, len(idx) - 1)]
        best = None
        for lb in lookbacks:
            strat = portfolio({a: trend_long_flat(s[s.index <= tr_hi], lb, cost)
                               for a, s in closes.items()})
            m = metrics(strat, ppy)
            if not m:
                continue
            if best is None or m["sharpe"] > best[0]:
                best = (m["sharpe"], lb)
        if best is None:
            continue
        lb = best[1]
        strat_full = portfolio({a: trend_long_flat(s, lb, cost) for a, s in closes.items()})
        te = strat_full[(strat_full.index > tr_hi) & (strat_full.index <= te_hi)]
        oos.append(te)
        picks.append(dict(fold=k, lookback=lb, test_end=str(te_hi)[:10]))
    if not oos:
        return None, picks
    return pd.concat(oos), picks


def run(universe: dict, label: str, cost: float, ppy: int, lookbacks: list):
    closes = {a: load_close(f) for a, f in universe.items() if (CACHE / f"{f}.csv").exists()}
    print(f"\n===== {label} =====")
    print("assets:", ", ".join(f"{a}({s.index[0].date()}→{s.index[-1].date()})"
                               for a, s in closes.items()))

    # Fixed sensible config (200-day trend) — full period, per asset + portfolio
    fixed_lb = 200 if ppy >= 300 else 150
    per_asset = {}
    for a, s in closes.items():
        st = metrics(trend_long_flat(s, fixed_lb, cost), ppy)
        bh = metrics(buy_hold(s), ppy)
        per_asset[a] = dict(trend=st, buyhold=bh)
        print(f"  {a}: TREND cagr={st['cagr_pct']}% sharpe={st['sharpe']} "
              f"maxDD={st['max_dd_pct']}% | BUY&HOLD cagr={bh['cagr_pct']}% "
              f"sharpe={bh['sharpe']} maxDD={bh['max_dd_pct']}%")

    port = portfolio({a: trend_long_flat(s, fixed_lb, cost) for a, s in closes.items()})
    bh_port = portfolio({a: buy_hold(s) for a, s in closes.items()})
    m_port = metrics(port, ppy)
    m_bh = metrics(bh_port, ppy)
    print(f"  PORTFOLIO TREND (lb={fixed_lb}): {m_port}")
    print(f"  PORTFOLIO BUY&HOLD:            {m_bh}")

    # Walk-forward OOS (honest)
    wf, picks = walk_forward(closes, cost, ppy, lookbacks)
    m_wf = metrics(wf, ppy) if wf is not None else {}
    # buy&hold over the same OOS window
    if wf is not None:
        bh_oos = bh_port[bh_port.index.isin(wf.index)]
        m_bh_oos = metrics(bh_oos, ppy)
    else:
        m_bh_oos = {}
    print(f"  WALK-FORWARD OOS TREND: {m_wf}")
    print(f"  WALK-FORWARD OOS BUY&HOLD: {m_bh_oos}")
    print(f"  WF picks: {[(p['fold'], p['lookback'], p['test_end']) for p in picks]}")

    return dict(label=label, fixed_lookback=fixed_lb, cost=cost,
                per_asset=per_asset, portfolio_trend=m_port, portfolio_buyhold=m_bh,
                wf_oos_trend=m_wf, wf_oos_buyhold=m_bh_oos, wf_picks=picks)


CRYPTO_BROAD = {
    "BTC": "binance_btc_usd_1d", "ETH": "binance_eth_usd_1d", "SOL": "binance_sol_usd_1d",
    "ADA": "binance_ada_usd_1d", "XRP": "binance_xrp_usd_1d",
    "DOGE": "binance_doge_usd_1d", "LINK": "binance_link_usd_1d",
}


def run_combined(cost: float, ppy: int = 365):
    """Flagship: diversified, inverse-vol, no-leverage trend across crypto+ETF."""
    universe = {**CRYPTO_BROAD, **ETF}
    closes = {a: load_close(f) for a, f in universe.items() if (CACHE / f"{f}.csv").exists()}
    print("\n===== KOMBINERAD all-weather trend (krypto spot + ETF, inv-vol, ingen hävstång) =====")
    print(f"  {len(closes)} tillgångar")

    def split_metrics(lb):
        strat = vol_weighted_trend(closes, lb, cost)
        return strat

    # fixed 150d, full period + walk-forward over lookbacks
    full = metrics(vol_weighted_trend(closes, 150, cost), ppy)
    print(f"  FULL (lb=150): {full}")
    # walk-forward
    idx = None
    for s in closes.values():
        idx = s.index if idx is None else idx.union(s.index)
    idx = idx.sort_values(); block = len(idx) // 5
    oos = []; picks = []
    for k in range(4):
        tr_hi = idx[(k + 1) * block - 1]; te_hi = idx[min((k + 2) * block - 1, len(idx) - 1)]
        best = None
        for lb in (100, 150, 200):
            m = metrics(vol_weighted_trend({a: s[s.index <= tr_hi] for a, s in closes.items()}, lb, cost), ppy)
            if m and (best is None or m["sharpe"] > best[0]):
                best = (m["sharpe"], lb)
        if best is None:
            continue
        full_s = vol_weighted_trend(closes, best[1], cost)
        oos.append(full_s[(full_s.index > tr_hi) & (full_s.index <= te_hi)])
        picks.append(dict(fold=k, lookback=best[1], test_end=str(te_hi)[:10]))
    wf = pd.concat(oos) if oos else None
    m_wf = metrics(wf, ppy) if wf is not None else {}
    print(f"  WALK-FORWARD OOS: {m_wf}")
    print(f"  WF picks: {[(p['fold'], p['lookback'], p['test_end']) for p in picks]}")
    return dict(full_lb150=full, wf_oos=m_wf, wf_picks=picks, n_assets=len(closes))


def current_stance(universe: dict, lookback: int) -> list:
    out = []
    for a, f in universe.items():
        if not (CACHE / f"{f}.csv").exists():
            continue
        s = load_close(f)
        ema = s.ewm(span=lookback, adjust=False).mean()
        in_trend = bool(s.iloc[-1] > ema.iloc[-1])
        gap = (s.iloc[-1] / ema.iloc[-1] - 1) * 100
        out.append((a, "HÅLL (uppåttrend)" if in_trend else "KONTANT (nedtrend)",
                    round(gap, 1), str(s.index[-1].date())))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--signal", action="store_true", help="dagens håll/kontant-läge")
    ap.add_argument("--crypto-cost", type=float, default=0.0015)  # 0.15%/switch spot
    ap.add_argument("--etf-cost", type=float, default=0.001)       # 0.10%/switch
    args = ap.parse_args()

    if args.signal:
        print("KRYPTO (spot/ETP) — 200-dagars trend:")
        for a, st, gap, d in current_stance(CRYPTO_BROAD, 200):
            print(f"  {a:5s} {st:20s}  ({gap:+.1f}% mot trend, per {d})")
        print("\nETF (Avanza/Nordnet) — 150-dagars trend:")
        for a, st, gap, d in current_stance(ETF, 150):
            print(f"  {a:5s} {st:20s}  ({gap:+.1f}% mot trend, per {d})")
        print("\nRegel: HÅLL = äg tillgången. KONTANT = stå utanför tills trenden vänder upp.")
        return

    results = {}
    results["crypto_spot"] = run(CRYPTO, "Krypto spot (long/flat trend) — 365d/yr",
                                 args.crypto_cost, 365, [50, 100, 150, 200])
    results["etf"] = run(ETF, "ETF-portfölj (long/flat trend) — 252d/yr",
                         args.etf_cost, 252, [100, 150, 200, 250])
    results["combined"] = run_combined(args.crypto_cost)

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, default=str))
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
