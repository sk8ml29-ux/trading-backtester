"""
Market-neutral cross-sectional crypto strategy — vectorized backtest.

Idea: rank a basket of liquid coins by recent performance and hold a
dollar-neutral book (long the strongest / short the weakest), rebalancing on a
fixed schedule. Being market-neutral, it targets *consistent* returns with low
beta to BTC and shallow drawdowns — the profile the mandate asks for.

Costs are charged on turnover (commission + slippage + half-spread per unit
notional traded). Signals use only past data; the forward return that realizes a
weight is the *next* bar's return (no look-ahead).

Run:
    python3 -m research.cross_sectional --bar 8h --start 2023-01-01
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"

# 15m base has full 2023-01 history for all seven majors; resampled to `bar`.
UNIVERSE = {
    "BTC": "binance_btc_usd_15m",
    "ETH": "binance_eth_usd_15m",
    "SOL": "binance_sol_usd_15m",
    "XRP": "binance_xrp_usd_15m",
    "ADA": "binance_ada_usd_15m",
    "DOGE": "binance_doge_usd_15m",
    "LINK": "binance_link_usd_15m",
}

COST_PER_SIDE = 0.0009   # commission 0.05% + slippage 0.02% + half-spread 0.02%
PPY = 365


def build_panel(bar: str, start: str | None, end: str | None) -> pd.DataFrame:
    closes = {}
    for label, fname in UNIVERSE.items():
        path = CACHE / f"{fname}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, parse_dates=["datetime"], index_col="datetime")
        df = df[~df.index.duplicated(keep="first")].sort_index()
        s = df["close"].resample(bar).last()
        closes[label] = s
    panel = pd.DataFrame(closes).dropna(how="all")
    if start:
        panel = panel[panel.index >= pd.Timestamp(start)]
    if end:
        panel = panel[panel.index < pd.Timestamp(end)]
    # require all symbols present (common window)
    panel = panel.dropna()
    return panel


def series_metrics(daily_ret: pd.Series, base_bar_ret: pd.Series | None = None) -> dict:
    dr = daily_ret.dropna()
    if len(dr) < 5:
        return dict(n_days=0)
    mean_r, std_r = dr.mean(), dr.std(ddof=0)
    downside = dr[dr < 0]
    dstd = downside.std(ddof=0) if len(downside) else 0.0
    sharpe = mean_r / std_r * math.sqrt(PPY) if std_r > 0 else 0.0
    sortino = mean_r / dstd * math.sqrt(PPY) if dstd > 0 else 0.0
    equity = (1 + dr).cumprod()
    roll = equity.cummax()
    dd = ((equity - roll) / roll).min()
    return dict(
        n_days=int(len(dr)),
        net_return_pct=round((equity.iloc[-1] - 1) * 100, 2),
        net_per_day_pct=round(mean_r * 100, 4),
        win_days_pct=round((dr > 0).mean() * 100, 2),
        sharpe=round(sharpe, 3),
        sortino=round(sortino, 3),
        max_dd_pct=round(dd * 100, 2),
        worst_day_pct=round(dr.min() * 100, 3),
        best_day_pct=round(dr.max() * 100, 3),
    )


def backtest(panel: pd.DataFrame, lookback: int, hold: int, k: int,
             direction: str, leverage: float, cost_per_side: float = COST_PER_SIDE,
             vol_target: float = 0.0, vol_window: int = 30, vol_cap: float = 3.0):
    """Return per-bar net return series and the daily-resampled series.

    ``vol_target`` (per-bar, e.g. 0.01 = 1%): if >0, scale the book by
    target/trailing-realized-vol (using only past info) to stabilize risk and
    cap drawdowns. The scaling is applied to weights, so turnover/costs scale too.
    """
    px = panel.to_numpy(float)
    T, N = px.shape
    ret = np.zeros_like(px)
    ret[1:] = px[1:] / px[:-1] - 1.0

    # signal = trailing return over lookback (momentum) or its negative (reversion)
    sig = np.full_like(px, np.nan)
    sig[lookback:] = px[lookback:] / px[:-lookback] - 1.0
    if direction == "reversion":
        sig = -sig

    W = np.zeros((T, N))
    cur = np.zeros(N)
    for t in range(T):
        if t >= lookback and t % hold == 0 and np.isfinite(sig[t]).sum() >= 2 * k:
            s = sig[t].copy()
            order = np.argsort(np.where(np.isfinite(s), s, -np.inf))
            longs = order[-k:]
            shorts = order[:k]
            cur = np.zeros(N)
            cur[longs] = leverage / (2 * k)
            cur[shorts] = -leverage / (2 * k)
        W[t] = cur

    if vol_target and vol_target > 0:
        Wprev0 = np.vstack([np.zeros((1, N)), W[:-1]])
        base_gross = (Wprev0 * ret).sum(axis=1)
        realized = pd.Series(base_gross).rolling(vol_window).std(ddof=0)
        scale = (vol_target / realized).clip(upper=vol_cap).shift(1).fillna(1.0)
        scale = scale.to_numpy()
        W = W * scale[:, None]

    # portfolio return at t uses weights set at t-1 (no look-ahead)
    Wprev = np.vstack([np.zeros((1, N)), W[:-1]])
    gross_ret = (Wprev * ret).sum(axis=1)
    turnover = np.abs(W - Wprev).sum(axis=1)
    cost = turnover * cost_per_side
    net = gross_ret - cost

    idx = panel.index
    net_s = pd.Series(net, index=idx)
    gross_s = pd.Series(gross_ret, index=idx)
    cost_s = pd.Series(cost, index=idx)
    daily = (1 + net_s).groupby(net_s.index.floor("D")).prod() - 1
    return dict(net=net_s, gross=gross_s, cost=cost_s, daily=daily)


def param_grid():
    return [
        dict(lookback=lb, hold=h, k=k, direction=d, leverage=lev, vol_target=vt)
        for d in ("momentum", "reversion")
        for lb in (3, 6, 12, 24, 48, 96)
        for h in (1, 3, 6, 12, 24)
        for k in (1, 2, 3)
        for lev in (1.0, 2.0)
        for vt in (0.0, 0.006, 0.01)
        if h <= lb * 3
    ]


def scorecard(m: dict) -> dict:
    checks = {
        "net_per_day>=0.25%": m.get("net_per_day_pct", 0) >= 0.25,
        "win_days>=60%": m.get("win_days_pct", 0) >= 60.0,
        "win_days>=50%_floor": m.get("win_days_pct", 0) >= 50.0,
        "max_dd<=10%": abs(m.get("max_dd_pct", 100)) <= 10.0,
        "worst_day>=-2%": m.get("worst_day_pct", -100) >= -2.0,
        "sharpe>=1.5": m.get("sharpe", 0) >= 1.5,
    }
    return {k: ("PASS" if v else "FAIL") for k, v in checks.items()}


def optimize(panel: pd.DataFrame, train_ratio: float, cost_per_side: float):
    n = len(panel)
    split = int(n * train_ratio)
    train, test = panel.iloc[:split], panel.iloc[split:]

    best = None
    for p in param_grid():
        r = backtest(train, cost_per_side=cost_per_side, **p)
        m = series_metrics(r["daily"])
        if m.get("n_days", 0) < 30:
            continue
        sc = m["sharpe"]
        if best is None or sc > best[0]:
            best = (sc, p, m)
    return best, train, test


def walk_forward(panel: pd.DataFrame, cost_per_side: float, n_folds: int = 4):
    """Anchored walk-forward: pick params on expanding in-sample, apply to next
    OOS block, concatenate all OOS daily returns. No look-ahead across folds."""
    n = len(panel)
    block = n // (n_folds + 1)
    oos_daily = []
    picks = []
    for f in range(n_folds):
        tr = panel.iloc[: (f + 1) * block]
        te = panel.iloc[(f + 1) * block : (f + 2) * block]
        if len(te) < block // 2:
            break
        best = None
        for p in param_grid():
            r = backtest(tr, cost_per_side=cost_per_side, **p)
            m = series_metrics(r["daily"])
            if m.get("n_days", 0) < 30:
                continue
            if best is None or m["sharpe"] > best[0]:
                best = (m["sharpe"], p)
        if best is None:
            continue
        p = best[1]
        r = backtest(te, cost_per_side=cost_per_side, **p)
        oos_daily.append(r["daily"])
        picks.append(dict(fold=f, test_start=str(te.index[0]), test_end=str(te.index[-1]),
                          params=p))
    if not oos_daily:
        return None, picks
    combined = pd.concat(oos_daily)
    combined = combined.groupby(combined.index).sum()  # in case of overlap edges
    return combined, picks


def evaluate_fixed(panel, p, train_ratio, cost_per_side):
    """Evaluate a single FIXED config: in-sample / out-of-sample / full / yearly.
    No per-fold re-optimization — the most honest way to state 'these parameters'."""
    n = len(panel)
    split = int(n * train_ratio)
    r_is = backtest(panel.iloc[:split], cost_per_side=cost_per_side, **p)
    r_oos = backtest(panel.iloc[split:], cost_per_side=cost_per_side, **p)
    r_full = backtest(panel, cost_per_side=cost_per_side, **p)
    gross_sum = r_oos["gross"].sum()
    cost_sum = r_oos["cost"].sum()
    cs = round(cost_sum / gross_sum * 100, 1) if gross_sum > 0 else float("inf")
    daily = r_oos["daily"]
    yearly = {str(yr): series_metrics(g) for yr, g in daily.groupby(daily.index.year)}
    return dict(
        params=p,
        is_metrics=series_metrics(r_is["daily"]),
        oos_metrics=series_metrics(r_oos["daily"]),
        oos_cost_share_pct=cs,
        oos_scorecard=scorecard(series_metrics(r_oos["daily"])),
        full_metrics=series_metrics(r_full["daily"]),
        yearly_oos=yearly,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bar", default="8h")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--train-ratio", type=float, default=0.55)
    ap.add_argument("--cost-per-side", type=float, default=COST_PER_SIDE)
    ap.add_argument("--out", default=None)
    ap.add_argument("--fixed", action="store_true",
                    help="evaluate a single fixed config (see --lb/--hold/...)")
    ap.add_argument("--signal", action="store_true",
                    help="print the current target book (paper-ready)")
    ap.add_argument("--lb", type=int, default=48)
    ap.add_argument("--hold", type=int, default=6)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--direction", default="momentum")
    ap.add_argument("--leverage", type=float, default=1.0)
    ap.add_argument("--vol-target", type=float, default=0.0)
    args = ap.parse_args()

    panel = build_panel(args.bar, args.start, args.end)
    print(f"panel: {panel.shape[0]} bars x {panel.shape[1]} symbols "
          f"{panel.index[0]}..{panel.index[-1]} bar={args.bar}")

    if args.signal:
        p = dict(lookback=args.lb, hold=args.hold, k=args.k, direction=args.direction)
        px = panel
        lb = p["lookback"]
        trailing = px.iloc[-1] / px.iloc[-1 - lb] - 1.0
        if p["direction"] == "reversion":
            trailing = -trailing
        ranked = trailing.sort_values(ascending=False)
        longs = ranked.index[: p["k"]].tolist()
        shorts = ranked.index[-p["k"]:].tolist()
        w = args.leverage / (2 * p["k"])
        print(f"\nAs of {px.index[-1]} (bar={args.bar}) — target dollar-neutral book:")
        print(f"  LONG  {p['k']}: " + ", ".join(f"{s} +{w:.3f}" for s in longs))
        print(f"  SHORT {p['k']}: " + ", ".join(f"{s} -{w:.3f}" for s in shorts))
        print(f"  trailing {lb}-bar returns: " +
              ", ".join(f"{s}={trailing[s]*100:.2f}%" for s in ranked.index))
        print(f"  rebalance every {p['hold']} bars ({p['hold']} x {args.bar}); "
              f"gross leverage {args.leverage:.2f}")
        return

    if args.fixed:
        p = dict(lookback=args.lb, hold=args.hold, k=args.k, direction=args.direction,
                 leverage=args.leverage, vol_target=args.vol_target)
        res = evaluate_fixed(panel, p, args.train_ratio, args.cost_per_side)
        print(f"\nFIXED config: {p}")
        print(f"IS   : {res['is_metrics']}")
        print(f"OOS  : {res['oos_metrics']}")
        print(f"OOS cost share: {res['oos_cost_share_pct']}%  scorecard: {res['oos_scorecard']}")
        print(f"FULL : {res['full_metrics']}")
        print("yearly OOS:")
        for yr, mm in res["yearly_oos"].items():
            print(f"  {yr}: net={mm.get('net_return_pct')}% per_day={mm.get('net_per_day_pct')}% "
                  f"win_days={mm.get('win_days_pct')}% sharpe={mm.get('sharpe')} dd={mm.get('max_dd_pct')}%")
        if args.out:
            Path(args.out).write_text(json.dumps(res, indent=2, default=str))
            print(f"wrote {args.out}")
        return

    best, train, test = optimize(panel, args.train_ratio, args.cost_per_side)
    if best is None:
        print("no viable params")
        return
    sc_is, p, m_is = best
    print(f"\nBEST params: {p}")
    print(f"IS  train: {m_is}")

    r = backtest(test, cost_per_side=args.cost_per_side, **p)
    m_oos = series_metrics(r["daily"])
    gross_sum = r["gross"].sum()
    cost_sum = r["cost"].sum()
    cost_share = round(cost_sum / gross_sum * 100, 1) if gross_sum > 0 else float("inf")
    print(f"OOS test:  {m_oos}")
    print(f"OOS cost share of gross: {cost_share}%")
    print(f"OOS scorecard: {scorecard(m_oos)}")

    # yearly OOS
    daily = r["daily"]
    yearly = {}
    for yr, g in daily.groupby(daily.index.year):
        yearly[str(yr)] = series_metrics(g)
    print("yearly OOS:")
    for yr, mm in yearly.items():
        print(f"  {yr}: net={mm.get('net_return_pct')}% per_day={mm.get('net_per_day_pct')}% "
              f"win_days={mm.get('win_days_pct')}% sharpe={mm.get('sharpe')} dd={mm.get('max_dd_pct')}%")

    # Rolling walk-forward across the whole history (re-optimized per fold)
    wf_daily, wf_picks = walk_forward(panel, args.cost_per_side, n_folds=4)
    wf_metrics = series_metrics(wf_daily) if wf_daily is not None else {}
    print(f"\nWALK-FORWARD (concatenated OOS): {wf_metrics}")
    if wf_metrics:
        print(f"WF scorecard: {scorecard(wf_metrics)}")
        for pk in wf_picks:
            print(f"  fold {pk['fold']}: {pk['test_start'][:10]}..{pk['test_end'][:10]} {pk['params']}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            dict(bar=args.bar, best_params=p, is_metrics=m_is, oos_metrics=m_oos,
                 oos_cost_share_pct=cost_share, oos_scorecard=scorecard(m_oos),
                 yearly_oos=yearly, wf_metrics=wf_metrics, wf_scorecard=scorecard(wf_metrics) if wf_metrics else {},
                 wf_picks=wf_picks), indent=2, default=str))
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
