"""
Funding-harvest improvement lab — 5 experimentation loops on top of the
delivered delta-neutral funding harvest (research/funding_harvest.py).

More honest simulator than the baseline: portfolio turnover cost is charged on
the change in each coin's *signed dollar weight* (so funding-weighted / top-N
allocation and rebalancing are costed correctly, not just sign flips).

Knobs explored:
  - alloc:   equal | funding | topn        (how to weight active coins)
  - topn:    keep only the N richest-funding coins
  - deploy:  active | fixed                 (redeploy leverage vs let capital idle)
  - predict: mean | ewma | last            (funding forecast)
  - adaptive threshold: enter = base + k * rolling std(funding)
  - basis filter: only take the leg the basis supports
  - compounding

Every idea is tested with FIXED params on a held-out OOS split (and the winner on
a full rolling walk-forward), so improvements are genuine, not overfit.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.funding_harvest import load_all, metrics, scorecard, LEG_COST, PPY
from research.binance_vision import BASKET, CACHE


# ---------------------------------------------------------------------------
# Per-coin precompute (once) for speed across experiments
# ---------------------------------------------------------------------------
def precompute(data: dict) -> dict:
    pc = {}
    for sym, df in data.items():
        f = df["f"].astype(float)
        P = df["P"].to_numpy(float)
        S = df["S"].to_numpy(float)
        pret = np.zeros(len(P)); pret[1:] = P[1:] / P[:-1] - 1
        sret = np.zeros(len(S)); sret[1:] = S[1:] / S[:-1] - 1
        basis = (P - S) / S
        # trailing funding volatility (shifted -> no look-ahead), for conviction scoring
        fvol = f.rolling(60, min_periods=10).std().shift(1).bfill().fillna(f.abs().mean()).to_numpy()
        pc[sym] = dict(idx=df.index, f=f.to_numpy(), pret=pret, sret=sret,
                       basis=basis, fvol=fvol)
    return pc


def _pred(f: np.ndarray, method: str, lookback: int) -> np.ndarray:
    s = pd.Series(f)
    if method == "last":
        return s.shift(0).to_numpy()
    if method == "ewma":
        return s.ewm(span=lookback, adjust=False).mean().to_numpy()
    return s.rolling(lookback, min_periods=1).mean().to_numpy()


def _hysteresis(pred: np.ndarray, enter: np.ndarray, exit_: np.ndarray,
                mode: str, basis: np.ndarray | None, basis_filter: bool) -> np.ndarray:
    n = len(pred)
    g = np.zeros(n)
    state = 0
    for i in range(n):
        p = pred[i]
        en = enter[i]; ex = exit_[i]
        if state == 0:
            if p > en and (not basis_filter or basis is None or basis[i] >= 0):
                state = 1
            elif mode == "both" and p < -en and (not basis_filter or basis is None or basis[i] <= 0):
                state = -1
        elif state == 1:
            if p < ex:
                state = 0
        elif state == -1:
            if p > -ex:
                state = 0
        g[i] = state
    return g


def build_signed_positions(pc: dict, p: dict):
    """Return dict sym -> signed g array (held into t, no look-ahead) and pred array."""
    G, PRED = {}, {}
    for sym, d in pc.items():
        f = d["f"]
        pred = _pred(f, p.get("predict", "mean"), p["lookback"])
        if p.get("adapt_k", 0.0) > 0:
            sd = pd.Series(f).rolling(p.get("adapt_win", 90), min_periods=10).std().fillna(0).to_numpy()
            enter = p["enter"] + p["adapt_k"] * sd
        else:
            enter = np.full(len(f), p["enter"])
        exit_ = np.full(len(f), p["exit_"])
        g_dec = _hysteresis(pred, enter, exit_, p["mode"],
                            d["basis"], p.get("basis_filter", False))
        g = np.zeros(len(f)); g[1:] = g_dec[:-1]
        G[sym] = g
        PRED[sym] = np.concatenate([[0.0], pred[:-1]])   # pred info available at t
    return G, PRED


def simulate(pc: dict, p: dict, leg_cost: float = LEG_COST):
    """Portfolio daily returns with signed-weight turnover costs."""
    G, PRED = build_signed_positions(pc, p)
    syms = list(pc.keys())
    # assemble on shared index
    frames_g, frames_pred, frames_f, frames_pr, frames_sr, frames_fv = {}, {}, {}, {}, {}, {}
    for sym in syms:
        idx = pc[sym]["idx"]
        frames_g[sym] = pd.Series(G[sym], index=idx)
        frames_pred[sym] = pd.Series(PRED[sym], index=idx)
        frames_f[sym] = pd.Series(pc[sym]["f"], index=idx)
        frames_pr[sym] = pd.Series(pc[sym]["pret"], index=idx)
        frames_sr[sym] = pd.Series(pc[sym]["sret"], index=idx)
        frames_fv[sym] = pd.Series(pc[sym]["fvol"], index=idx)
    Gm = pd.DataFrame(frames_g).sort_index()
    idx = Gm.index
    Pred = pd.DataFrame(frames_pred).reindex(idx).fillna(0.0)
    Fvol = pd.DataFrame(frames_fv).reindex(idx).ffill().fillna(1e9)
    Fm = pd.DataFrame(frames_f).reindex(idx).fillna(0.0)
    Pr = pd.DataFrame(frames_pr).reindex(idx).fillna(0.0)
    Sr = pd.DataFrame(frames_sr).reindex(idx).fillna(0.0)
    Gm = Gm.fillna(0.0)

    active = (Gm != 0).astype(float)
    # allocation magnitude m (per coin, >=0)
    alloc = p.get("alloc", "equal")
    if alloc == "funding":
        mag = (Pred.abs() * active)
    elif alloc == "topn":
        N = p.get("topn", 6)
        rank = (Pred.abs() * active).rank(axis=1, ascending=False, method="first")
        mag = ((rank <= N) & (active > 0)).astype(float)
    elif alloc == "conviction":
        # score = stable carry = predicted funding / trailing funding vol.
        # Map each active coin's cross-sectional rank to a per-position leverage
        # multiplier in [conv_min, conv_max] (e.g. 1..10). High, STABLE carry
        # gets the most; noisy/low carry gets the least.
        cmin = p.get("conv_min", 1.0)
        cmax = p.get("conv_max", 10.0)
        score = (Pred.abs() / (Fvol.abs() + 1e-9)) * active
        # rank within each row among active coins -> [0,1] -> [cmin,cmax]
        r = score.where(active > 0).rank(axis=1, pct=True)
        mag = (cmin + (cmax - cmin) * r).where(active > 0, 0.0).fillna(0.0)
    else:  # equal
        mag = active.copy()

    denom = mag.sum(axis=1)
    deploy = p.get("deploy", "active")
    lev = p["leverage"]
    if deploy == "fixed":
        slots = p.get("slots", 6)
        denom_use = denom.clip(lower=slots)
    else:
        denom_use = denom.replace(0.0, np.nan)
    absW = mag.div(denom_use, axis=0).fillna(0.0) * lev
    SW = absW * Gm                                    # signed weight (+short-perp / -long-perp)

    funding_pnl = (SW * Fm).sum(axis=1)
    basis_pnl = (SW * (Sr - Pr)).sum(axis=1)
    dSW = SW.diff().abs().sum(axis=1).fillna(SW.abs().sum(axis=1))
    cost = dSW * leg_cost
    step = funding_pnl + basis_pnl - cost

    if p.get("compound", False):
        # convert per-8h step returns into compounded daily
        daily = (1 + step).groupby(step.index.floor("D")).prod() - 1
    else:
        daily = step.groupby(step.index.floor("D")).sum()

    diag = dict(gross_funding=float(funding_pnl.clip(lower=0).sum()),
                net_funding=float(funding_pnl.sum()),
                total_cost=float(cost.sum()),
                avg_active=float(active.sum(axis=1).mean()),
                avg_gross_exposure=float(SW.abs().sum(axis=1).mean()))
    return daily, diag


def apply_vol_target(daily: pd.Series, target_daily_vol: float, window: int = 30,
                     cap: float = 10.0) -> pd.Series:
    """Scale the (linear) daily return series to a target daily vol using only
    trailing info. Because funding/basis/cost all scale linearly with position
    size, scaling the daily series is equivalent to scaling the book."""
    vol = daily.rolling(window).std(ddof=0)
    scale = (target_daily_vol / vol).clip(upper=cap).shift(1).fillna(1.0)
    return daily * scale


def split_eval(pc: dict, p: dict, train_ratio=0.55, leg_cost=LEG_COST):
    # shared time index
    idx = None
    for d in pc.values():
        idx = d["idx"] if idx is None else idx.union(d["idx"])
    idx = idx.sort_values()
    split = idx[int(len(idx) * train_ratio)]
    def sub(rng):
        out = {}
        for s, d in pc.items():
            mask = (d["idx"] > rng[0]) & (d["idx"] <= rng[1])
            if mask.sum() > 100:
                out[s] = dict(idx=d["idx"][mask], f=d["f"][mask],
                              pret=d["pret"][mask], sret=d["sret"][mask],
                              basis=d["basis"][mask], fvol=d["fvol"][mask])
        return out
    lo = idx[0] - pd.Timedelta(days=1)
    is_pc = sub((lo, split))
    oos_pc = sub((split, idx[-1]))
    d_is, _ = simulate(is_pc, p, leg_cost)
    d_oos, diag = simulate(oos_pc, p, leg_cost)
    return metrics(d_is), metrics(d_oos), diag, d_oos


def walk_forward(pc: dict, grid: list, leg_cost=LEG_COST, n_folds=4):
    idx = None
    for d in pc.values():
        idx = d["idx"] if idx is None else idx.union(d["idx"])
    idx = idx.sort_values()
    block = len(idx) // (n_folds + 1)
    def sub(lo, hi):
        out = {}
        for s, d in pc.items():
            mask = (d["idx"] > lo) & (d["idx"] <= hi)
            if mask.sum() > 100:
                out[s] = dict(idx=d["idx"][mask], f=d["f"][mask],
                              pret=d["pret"][mask], sret=d["sret"][mask],
                              basis=d["basis"][mask], fvol=d["fvol"][mask])
        return out
    oos = []
    picks = []
    for k in range(n_folds):
        tr_lo = idx[0] - pd.Timedelta(days=1)
        tr_hi = idx[(k + 1) * block - 1]
        te_hi = idx[min((k + 2) * block - 1, len(idx) - 1)]
        tr = sub(tr_lo, tr_hi); te = sub(tr_hi, te_hi)
        best = None
        for p in grid:
            d_tr, _ = simulate(tr, p, leg_cost)
            m = metrics(d_tr)
            if m.get("n_days", 0) < 30:
                continue
            if best is None or m["sharpe"] > best[0]:
                best = (m["sharpe"], p)
        if best is None:
            continue
        d_te, _ = simulate(te, best[1], leg_cost)
        oos.append(d_te)
        picks.append(dict(fold=k, test_end=str(te_hi)[:10], params=best[1]))
    if not oos:
        return None, picks
    comb = pd.concat(oos); comb = comb.groupby(comb.index).sum()
    return comb, picks


BASE = dict(lookback=12, enter=0.0001, exit_=0.0, mode="both", leverage=1.0,
            alloc="equal", deploy="active", predict="mean", adapt_k=0.0,
            basis_filter=False, compound=False)

# Champion config after the 5 improvement loops (equal-weight, fixed-slot deploy,
# 24-period mean funding forecast, basis-consistency filter, broad universe).
CHAMPION = dict(lookback=24, enter=0.00015, exit_=0.0, mode="both", leverage=1.0,
                alloc="equal", deploy="fixed", slots=12, predict="mean",
                adapt_k=0.0, basis_filter=True, compound=False)


def discover_coins() -> list[str]:
    """All coins that have funding + perp + spot 8h caches downloaded."""
    import glob
    coins = []
    for f in sorted(glob.glob(str(CACHE / "vision_funding_*.csv"))):
        c = f.split("vision_funding_")[-1][:-4].upper()
        if (CACHE / f"vision_perp_{c.lower()}_8h.csv").exists() and \
           (CACHE / f"vision_spot_{c.lower()}_8h.csv").exists():
            coins.append(c)
    return coins


def show(tag, m_is, m_oos, diag):
    cs = round(diag["total_cost"] / diag["gross_funding"] * 100, 1) if diag["gross_funding"] > 0 else float("inf")
    print(f"[{tag}]")
    print(f"   IS : ann={m_is.get('ann_return_pct')}% sharpe={m_is.get('sharpe')} "
          f"win={m_is.get('win_days_pct')}% dd={m_is.get('max_dd_pct')}%")
    print(f"   OOS: ann={m_oos.get('ann_return_pct')}% sharpe={m_oos.get('sharpe')} "
          f"win={m_oos.get('win_days_pct')}% active_win={m_oos.get('active_win_days_pct')}% "
          f"dd={m_oos.get('max_dd_pct')}% worst={m_oos.get('worst_day_pct')}% "
          f"cost/gross={cs}% avg_active={round(diag['avg_active'],1)}")
    return m_oos


def build_signal_book(pc: dict, p: dict):
    """Current target delta-neutral book from the champion positions/weights."""
    G, PRED = build_signed_positions(pc, p)
    syms = list(pc.keys())
    last_g = {s: G[s][-1] for s in syms}
    active = [s for s in syms if last_g[s] != 0]
    # emulate fixed-slot equal weighting for the last bar
    denom = max(len(active), p.get("slots", 12))
    w = p["leverage"] / denom if denom else 0.0
    book = []
    for s in active:
        g = last_g[s]
        side = "SHORT perp / LONG spot" if g > 0 else "LONG perp / SHORT spot"
        pred = PRED[s][-1] * 100
        book.append((s, side, round(pred, 4), round(w, 4)))
    return book


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", default=None, help="comma list; default = all downloaded")
    ap.add_argument("--champion", action="store_true", help="evaluate champion (OOS + WF + leverage sweep)")
    ap.add_argument("--signal", action="store_true", help="print current champion target book")
    ap.add_argument("--leverage", type=float, default=1.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    coins = args.coins.split(",") if args.coins else discover_coins()
    data = load_all(coins)
    pc = precompute(data)
    print(f"loaded {len(data)} coins")

    if args.signal:
        p = dict(CHAMPION); p["leverage"] = args.leverage
        book = build_signal_book(pc, p)
        last_ts = max(d["idx"][-1] for d in pc.values())
        print(f"\nChampion target delta-neutral book (as of {last_ts}):")
        for s, side, pred, w in sorted(book, key=lambda x: -abs(x[2])):
            print(f"  {s:12s} {side:26s} pred_funding/8h={pred}%  weight={w}")
        print(f"  ({len(book)} legs; gross leverage {args.leverage:.1f}; rebalance every 8h)")
        return

    if args.champion:
        m_is, m_oos, diag, doos = split_eval(pc, CHAMPION)
        show("CHAMPION (single 55/45 split)", m_is, m_oos, diag)
        print("  scorecard(OOS 1x):", scorecard(m_oos))
        # walk-forward with a small robust grid
        grid = [dict(CHAMPION, lookback=lb, slots=s, enter=en)
                for lb in (18, 24) for s in (10, 12) for en in (0.0001, 0.00015)]
        wf, picks = walk_forward(pc, grid, n_folds=4)
        mwf = metrics(wf)
        print(f"\nWALK-FORWARD OOS 1x: ann={mwf['ann_return_pct']}% sharpe={mwf['sharpe']} "
              f"win={mwf['win_days_pct']}% dd={mwf['max_dd_pct']}% worst={mwf['worst_day_pct']}%")
        print("  scorecard(WF 1x):", scorecard(mwf))
        print("  leverage sweep (walk-forward OOS):")
        sweep = {}
        for lev in (1, 3, 5, 8, 10):
            mm = metrics(wf * lev)
            sweep[f"{lev}x"] = dict(ann=mm["ann_return_pct"], per_day=mm["net_per_day_pct"],
                                    dd=mm["max_dd_pct"], worst=mm["worst_day_pct"],
                                    win=mm["win_days_pct"], sharpe=mm["sharpe"])
            print(f"    {lev}x: ann={mm['ann_return_pct']}% per_day={mm['net_per_day_pct']}% "
                  f"dd={mm['max_dd_pct']}% worst={mm['worst_day_pct']}% win={mm['win_days_pct']}%")
        # cost stress
        _, m_stress, dg_s, _ = split_eval(pc, CHAMPION, leg_cost=0.0022)
        print(f"\n  cost-stress (taker-only 0.44% RT) OOS: ann={m_stress['ann_return_pct']}% "
              f"sharpe={m_stress['sharpe']} dd={m_stress['max_dd_pct']}%")
        if args.out:
            import json
            out = dict(n_coins=len(data), champion=CHAMPION,
                       oos_split_1x=m_oos, oos_scorecard_1x=scorecard(m_oos),
                       walk_forward_1x=mwf, wf_scorecard_1x=scorecard(mwf),
                       wf_leverage_sweep=sweep, wf_picks=picks,
                       cost_stress_oos=m_stress)
            Path(args.out).write_text(json.dumps(out, indent=2, default=str))
            print(f"  wrote {args.out}")
        return

    # default: baseline vs champion quick compare
    m_is, m_oos, diag, _ = split_eval(pc, BASE)
    show("BASELINE equal/active mean lb12 en1e-4 both", m_is, m_oos, diag)
    m_is, m_oos, diag, _ = split_eval(pc, CHAMPION)
    show("CHAMPION equal/fixed12 mean lb24 basis both", m_is, m_oos, diag)


if __name__ == "__main__":
    main()
