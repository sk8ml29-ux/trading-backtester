"""
Delta-neutral funding-rate harvest — the real, cost-surviving crypto edge.

Position per coin is market-neutral: to collect a POSITIVE funding rate you hold
(short perp + long spot); to collect a NEGATIVE funding rate you hold
(long perp + short spot). Being delta-neutral, directional price risk cancels
(only the tiny perp-vs-spot basis remains, ~4 bps std for majors), so the P&L is
dominated by funding carry — which Binance pays every 8h and which is positive
~85% of the time for BTC.

Honest accounting per 8h step and coin (capital per coin = 1):
    g in {-1,0,+1}          # +1 = short-perp/long-spot (receive +funding)
    funding_pnl = g * f_{t+1}
    basis_pnl   = g * (spot_ret - perp_ret)      # ~0, uses real prices
    cost        = |g_t - g_{t-1}| * leg_cost      # fees+slippage on both legs
Portfolio deploys `leverage` across the coins that have a position.

Signals use only past funding (trailing mean); realized funding/prices are next
step -> no look-ahead. Validated with train/OOS split, rolling walk-forward,
and a PASS/FAIL scorecard.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from research.binance_vision import CACHE, BASKET

PPY = 365
# Conservative Binance costs per leg (fee + slippage), as fraction of notional.
PERP_LEG = 0.00060     # 0.045% taker + 0.015% slippage
SPOT_LEG = 0.00090     # 0.075% taker (BNB discount) + 0.015% slippage
LEG_COST = PERP_LEG + SPOT_LEG   # cost to turn over one full delta-neutral unit


def load_coin(sym: str):
    """Return aligned DataFrame [f, P, S] on the 8h funding grid, or None."""
    fp = CACHE / f"vision_funding_{sym.lower()}.csv"
    pp = CACHE / f"vision_perp_{sym.lower()}_8h.csv"
    sp = CACHE / f"vision_spot_{sym.lower()}_8h.csv"
    if not (fp.exists() and pp.exists() and sp.exists()):
        return None
    f = pd.read_csv(fp, parse_dates=["time"], index_col="time")["funding_rate"]
    P = pd.read_csv(pp, parse_dates=["time"], index_col="time")["close"]
    S = pd.read_csv(sp, parse_dates=["time"], index_col="time")["close"]
    f = f[~f.index.duplicated()].sort_index()
    P = P[~P.index.duplicated()].sort_index()
    S = S[~S.index.duplicated()].sort_index()
    # snap funding times (which can carry ms) to the hour grid
    f.index = f.index.round("h")
    P.index = P.index.round("h")
    S.index = S.index.round("h")
    df = pd.DataFrame({"f": f}).join(pd.DataFrame({"P": P}), how="inner").join(
        pd.DataFrame({"S": S}), how="inner")
    df = df.dropna()
    return df if len(df) > 200 else None


def coin_signal(f: pd.Series, lookback: int, enter: float, exit_: float,
                mode: str) -> np.ndarray:
    """Hysteresis state machine on trailing-mean funding (decided at t).

    Enter the receive side only when trailing funding clearly beats the cost
    hurdle (|pred| > enter); hold until it decays past `exit_`. This keeps
    turnover low so the ~0.30% round-trip cost is amortized over long holds.
    """
    pred = f.rolling(lookback, min_periods=1).mean().to_numpy()
    n = len(pred)
    g = np.zeros(n)
    state = 0
    for i in range(n):
        p = pred[i]
        if state == 0:
            if p > enter:
                state = 1
            elif mode == "both" and p < -enter:
                state = -1
        elif state == 1:
            if p < exit_:
                state = 0
        elif state == -1:
            if p > -exit_:
                state = 0
        g[i] = state
    return g


def simulate(coins_data: dict, lookback: int, enter: float, exit_: float, mode: str,
             leverage: float, leg_cost: float = LEG_COST):
    """Return a daily return Series and a diagnostics dict."""
    # Build a per-coin step-return frame on a shared time index
    step_returns = {}
    funding_only = {}
    turnover_cost = {}
    active_flag = {}
    for sym, df in coins_data.items():
        f = df["f"]
        P = df["P"].to_numpy(float)
        S = df["S"].to_numpy(float)
        g_dec = coin_signal(f, lookback, enter, exit_, mode)   # decided at t
        g = np.zeros(len(f))
        g[1:] = g_dec[:-1]                              # position held into t (no look-ahead)
        fr = f.to_numpy(float)
        perp_ret = np.zeros(len(f)); perp_ret[1:] = P[1:] / P[:-1] - 1
        spot_ret = np.zeros(len(f)); spot_ret[1:] = S[1:] / S[:-1] - 1
        funding_pnl = g * fr                            # receive g*funding at t
        basis_pnl = g * (spot_ret - perp_ret)
        dturn = np.abs(np.diff(np.concatenate([[0.0], g])))
        cost = dturn * leg_cost
        ret = funding_pnl + basis_pnl - cost
        idx = f.index
        step_returns[sym] = pd.Series(ret, index=idx)
        funding_only[sym] = pd.Series(funding_pnl, index=idx)
        turnover_cost[sym] = pd.Series(cost, index=idx)
        active_flag[sym] = pd.Series(np.abs(g), index=idx)

    R = pd.DataFrame(step_returns).sort_index()
    A = pd.DataFrame(active_flag).reindex(R.index).fillna(0.0)
    F = pd.DataFrame(funding_only).reindex(R.index).fillna(0.0)
    Cst = pd.DataFrame(turnover_cost).reindex(R.index).fillna(0.0)

    n_active = A.sum(axis=1).replace(0, np.nan)
    w = leverage / n_active                              # equal weight across active coins
    port_step = (R.mul(w, axis=0)).sum(axis=1).fillna(0.0)
    port_fund = (F.mul(w, axis=0)).sum(axis=1).fillna(0.0)
    port_cost = (Cst.mul(w, axis=0)).sum(axis=1).fillna(0.0)

    daily = port_step.groupby(port_step.index.floor("D")).sum()
    diag = dict(
        gross_funding=float(port_fund.sum()),
        total_cost=float(port_cost.sum()),
        avg_active=float(A.sum(axis=1).mean()),
    )
    return daily, diag


def metrics(daily: pd.Series) -> dict:
    dr = daily.dropna()
    if len(dr) < 10:
        return dict(n_days=0)
    mean_r, std_r = dr.mean(), dr.std(ddof=0)
    downside = dr[dr < 0]
    dstd = downside.std(ddof=0) if len(downside) else 0.0
    equity = (1 + dr).cumprod()
    dd = ((equity - equity.cummax()) / equity.cummax()).min()
    active = dr != 0
    n_active = int(active.sum())
    return dict(
        n_days=int(len(dr)),
        net_return_pct=round((equity.iloc[-1] - 1) * 100, 2),
        net_per_day_pct=round(mean_r * 100, 4),
        ann_return_pct=round(((equity.iloc[-1]) ** (PPY / len(dr)) - 1) * 100, 2),
        win_days_pct=round((dr > 0).mean() * 100, 2),
        active_win_days_pct=round(((dr > 0) & active).sum() / n_active * 100, 2) if n_active else 0.0,
        active_days_pct=round(n_active / len(dr) * 100, 1),
        sharpe=round(mean_r / std_r * math.sqrt(PPY), 3) if std_r > 0 else 0.0,
        sortino=round(mean_r / dstd * math.sqrt(PPY), 3) if dstd > 0 else 0.0,
        max_dd_pct=round(dd * 100, 2),
        worst_day_pct=round(dr.min() * 100, 3),
        best_day_pct=round(dr.max() * 100, 3),
    )


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


def load_all(coins: list[str]) -> dict:
    data = {}
    for c in coins:
        d = load_coin(c)
        if d is not None:
            data[c] = d
    return data


GRID = [
    dict(lookback=lb, enter=en, exit_=ex, mode=mode, leverage=lev)
    for mode in ("positive_only", "both")
    for lb in (3, 6, 9, 12)
    for en in (0.00004, 0.00007, 0.0001, 0.00015)
    for ex in (0.0, 0.00002)
    for lev in (1.0,)
    if ex < en
]


def walk_forward(data, leg_cost, n_folds=4):
    # common time index across coins
    idx = None
    for d in data.values():
        idx = d.index if idx is None else idx.union(d.index)
    idx = idx.sort_values()
    block = len(idx) // (n_folds + 1)
    oos = []
    picks = []
    for fdx in range(n_folds):
        tr_end = idx[(fdx + 1) * block - 1]
        te_end = idx[min((fdx + 2) * block - 1, len(idx) - 1)]
        tr = {s: d[d.index <= tr_end] for s, d in data.items()}
        te = {s: d[(d.index > tr_end) & (d.index <= te_end)] for s, d in data.items()}
        best = None
        for p in GRID:
            daily, _ = simulate(tr, leg_cost=leg_cost, **p)
            m = metrics(daily)
            if m.get("n_days", 0) < 30:
                continue
            if best is None or m["sharpe"] > best[0]:
                best = (m["sharpe"], p)
        if best is None:
            continue
        daily, _ = simulate(te, leg_cost=leg_cost, **best[1])
        oos.append(daily)
        picks.append(dict(fold=fdx, test_end=str(te_end)[:10], params=best[1]))
    if not oos:
        return None, picks
    combined = pd.concat(oos)
    combined = combined.groupby(combined.index).sum()
    return combined, picks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", default=",".join(BASKET))
    ap.add_argument("--train-ratio", type=float, default=0.55)
    ap.add_argument("--leg-cost", type=float, default=LEG_COST)
    ap.add_argument("--lookback", type=int, default=6)
    ap.add_argument("--enter", type=float, default=0.00007)
    ap.add_argument("--exit", dest="exit_", type=float, default=0.0)
    ap.add_argument("--mode", default="positive_only")
    ap.add_argument("--leverage", type=float, default=1.0)
    ap.add_argument("--fixed", action="store_true")
    ap.add_argument("--signal", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    coins = args.coins.split(",")
    data = load_all(coins)
    print(f"loaded {len(data)} coins: {list(data.keys())}")
    if not data:
        print("no data — run: python3 -m research.binance_vision")
        return

    if args.signal:
        rows = []
        for s, d in data.items():
            g = coin_signal(d["f"], args.lookback, args.enter, args.exit_, args.mode)[-1]
            pred = d["f"].rolling(args.lookback, min_periods=1).mean().iloc[-1]
            if g != 0:
                side = "SHORT perp / LONG spot" if g > 0 else "LONG perp / SHORT spot"
                rows.append((s, side, round(pred * 100, 4)))
        print(f"\nTarget delta-neutral book (as of {list(data.values())[0].index[-1]}):")
        for s, side, pr in sorted(rows, key=lambda x: -abs(x[2])):
            print(f"  {s:10s} {side:26s} pred_funding/8h={pr}%")
        print(f"  ({len(rows)} active legs, weight {args.leverage/max(1,len(rows)):.3f} each)")
        return

    if args.fixed:
        p = dict(lookback=args.lookback, enter=args.enter, exit_=args.exit_,
                 mode=args.mode, leverage=args.leverage)
        idx = None
        for d in data.values():
            idx = d.index if idx is None else idx.union(d.index)
        idx = idx.sort_values()
        split = idx[int(len(idx) * args.train_ratio)]
        tr = {s: d[d.index <= split] for s, d in data.items()}
        te = {s: d[d.index > split] for s, d in data.items()}
        d_all, diag = simulate(data, leg_cost=args.leg_cost, **p)
        d_is, _ = simulate(tr, leg_cost=args.leg_cost, **p)
        d_oos, diag_oos = simulate(te, leg_cost=args.leg_cost, **p)
        m_is, m_oos, m_full = metrics(d_is), metrics(d_oos), metrics(d_all)
        cs = round(diag_oos["total_cost"] / diag_oos["gross_funding"] * 100, 1) if diag_oos["gross_funding"] > 0 else float("inf")
        print(f"\nFIXED {p}  leg_cost={args.leg_cost}")
        print(f"IS  : {m_is}")
        print(f"OOS : {m_oos}")
        print(f"OOS cost/gross-funding: {cs}%   avg active coins: {round(diag_oos['avg_active'],1)}")
        print(f"OOS scorecard: {scorecard(m_oos)}")
        print(f"FULL: {m_full}")
        yearly = {str(y): metrics(g) for y, g in d_oos.groupby(d_oos.index.year)}
        for y, mm in yearly.items():
            print(f"  {y}: net={mm.get('net_return_pct')}% per_day={mm.get('net_per_day_pct')}% "
                  f"win={mm.get('win_days_pct')}% active_win={mm.get('active_win_days_pct')}% "
                  f"sharpe={mm.get('sharpe')} dd={mm.get('max_dd_pct')}%")
        # Leverage sweep on the OOS book (market-neutral -> leverage is the return dial)
        print("leverage sweep (OOS):")
        base = d_oos.copy()
        for lev in (1, 2, 3, 5, 8):
            mm = metrics(base * lev)
            print(f"  {lev}x: net={mm['net_return_pct']}% ann={mm['ann_return_pct']}% "
                  f"per_day={mm['net_per_day_pct']}% dd={mm['max_dd_pct']}% "
                  f"worst_day={mm['worst_day_pct']}% sharpe={mm['sharpe']}")
        if args.out:
            Path(args.out).write_text(json.dumps(dict(
                params=p, leg_cost=args.leg_cost, is_metrics=m_is, oos_metrics=m_oos,
                oos_cost_share_pct=cs, oos_scorecard=scorecard(m_oos), full_metrics=m_full,
                yearly_oos=yearly), indent=2, default=str))
            print(f"wrote {args.out}")
        return

    # default: grid-search IS, evaluate OOS, plus walk-forward
    idx = None
    for d in data.values():
        idx = d.index if idx is None else idx.union(d.index)
    idx = idx.sort_values()
    split = idx[int(len(idx) * args.train_ratio)]
    tr = {s: d[d.index <= split] for s, d in data.items()}
    te = {s: d[d.index > split] for s, d in data.items()}
    best = None
    for p in GRID:
        daily, _ = simulate(tr, leg_cost=args.leg_cost, **p)
        m = metrics(daily)
        if m.get("n_days", 0) < 30:
            continue
        if best is None or m["sharpe"] > best[0]:
            best = (m["sharpe"], p, m)
    print(f"\nBEST IS params: {best[1]}")
    print(f"IS : {best[2]}")
    d_oos, diag = simulate(te, leg_cost=args.leg_cost, **best[1])
    m_oos = metrics(d_oos)
    print(f"OOS: {m_oos}")
    print(f"OOS scorecard: {scorecard(m_oos)}")
    wf, picks = walk_forward(data, args.leg_cost, n_folds=4)
    if wf is not None:
        print(f"\nWALK-FORWARD OOS: {metrics(wf)}")
        print(f"WF scorecard: {scorecard(metrics(wf))}")
        for pk in picks:
            print(f"  {pk}")


if __name__ == "__main__":
    main()
