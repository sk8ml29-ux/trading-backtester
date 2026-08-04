"""
Daily cross-sectional signals from order flow and basis.

Frequency is closed off as a route to profit: at retail taker rates an hourly
cross-section pays well over 100% a year in costs and no signal survives that.
Daily rebalancing costs roughly 7%, which is affordable, so the question
becomes whether there is anything in this data that works slowly.

Price history at daily frequency is picked over by everyone. What is not is the
microstructure underneath it, which the minute dumps expose for free:

  FLOW         the taker-buy split says how much of the day's volume was
               aggressive buying. Price tells you where the asset went; flow
               tells you whether anyone had to pay up to get it there.

  BASIS        the premium index prices the perpetual against spot all day, so
               a coin can be revealed as persistently bid in derivatives even
               when its price went nowhere.

  TRADE SIZE   dollar volume divided by trade count. A drift toward many small
               orders is a change in who is trading, not what it is worth.

  DIVERGENCE   the part of a coin's move that its own flow does not explain.
               A rally nobody had to pay for is a thin rally.

Every feature is trailing, cross-sectionally ranked and tested against forward
returns in and out of sample.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from scipy.special import erfinv

from .panel import load

BAR_PER_DAY = 24


def _xs_z(df: pd.DataFrame, clip: float = 3.0, min_names: int = 10) -> pd.DataFrame:
    n = df.notna().sum(axis=1)
    r = df.rank(axis=1)
    u = r.div(n + 1.0, axis=0).clip(1e-6, 1 - 1e-6)
    z = np.sqrt(2.0) * erfinv(2.0 * u - 1.0)
    return z.where(n.ge(min_names)).clip(-clip, clip)


def to_daily(mats: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Aggregate hourly matrices to daily, keeping flow and basis detail."""
    o, c = mats["open"], mats["close"]
    qv, cnt, ofi = mats["qvol"], mats["cnt"], mats["prem"] * 0 + mats["ofi"]
    prem, prem_x = mats["prem"], mats["prem_x"]

    day = pd.Grouper(freq="1D")
    out = {}
    out["open"] = o.groupby(day).first()
    out["close"] = c.groupby(day).last()
    out["qvol"] = qv.groupby(day).sum(min_count=6)
    out["cnt"] = cnt.groupby(day).sum(min_count=6)
    # Signed aggressive dollar flow, reconstructed from the hourly imbalance.
    signed = (ofi * qv)
    out["net_flow"] = signed.groupby(day).sum(min_count=6)
    out["prem"] = prem.groupby(day).mean()
    out["prem_min"] = prem_x.groupby(day).min()
    out["prem_sd"] = prem.groupby(day).std()
    out["ofi_sd"] = ofi.groupby(day).std()
    return out


def features(d: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    c, o = d["close"], d["open"]
    qv, cnt, nf = d["qvol"], d["cnt"], d["net_flow"]
    ln = np.log(c.replace(0, np.nan))
    ret1 = ln.diff()

    f: dict[str, pd.DataFrame] = {}

    # --- flow -------------------------------------------------------------
    for k in (1, 3, 7, 14):
        fl = nf.rolling(k, min_periods=k).sum()
        vol = qv.rolling(k, min_periods=k).sum()
        f[f"flow_{k}d"] = fl.div(vol)                       # share of volume
        base = qv.rolling(30, min_periods=15).median().shift(1) * k
        f[f"flowabs_{k}d"] = fl.div(base)                   # size vs normal turnover

    # --- basis ------------------------------------------------------------
    for k in (1, 3, 7, 14):
        f[f"prem_{k}d"] = d["prem"].rolling(k, min_periods=k).mean()
    f["prem_chg"] = d["prem"].rolling(3, min_periods=3).mean() \
        - d["prem"].rolling(14, min_periods=7).mean()
    psd = d["prem"].rolling(30, min_periods=15).std().shift(1)
    f["prem_z"] = d["prem"].div(psd)
    f["prem_dislo"] = d["prem_min"].div(psd)
    f["prem_vol"] = d["prem_sd"].rolling(7, min_periods=4).mean()

    # --- participant mix ---------------------------------------------------
    size = qv.div(cnt.replace(0, np.nan))
    f["trade_size"] = size
    f["size_shift"] = np.log(size.rolling(3, min_periods=2).mean()
                             .div(size.rolling(30, min_periods=15).mean()))
    f["cnt_burst"] = np.log(cnt.rolling(3, min_periods=2).mean()
                            .div(cnt.rolling(30, min_periods=15).mean()))
    f["vol_burst"] = np.log(qv.rolling(3, min_periods=2).mean()
                            .div(qv.rolling(30, min_periods=15).mean()))

    # --- divergence: the move flow does not account for ---------------------
    for k in (3, 7):
        r = ln.diff(k)
        fl = f[f"flow_{k}d"]
        # Cross-sectional regression of return on flow, per day; the residual is
        # the part of the move that nobody had to pay up for.
        rz, fz = _xs_z(r), _xs_z(fl)
        beta = (rz * fz).mean(axis=1) / (fz * fz).mean(axis=1)
        f[f"diverge_{k}d"] = rz.sub(fz.mul(beta, axis=0))

    # --- plain price controls ---------------------------------------------
    for k in (1, 3, 7, 14, 30):
        f[f"mom_{k}d"] = ln.diff(k)
    f["rvol_14d"] = ret1.rolling(14, min_periods=7).std()
    f["rvol_30d"] = ret1.rolling(30, min_periods=15).std()

    return f


def forward_returns(d: dict[str, pd.DataFrame], horizons=(1, 2, 3, 5, 10)) -> dict:
    o = d["open"]
    entry = o.shift(-1)
    return {h: (o.shift(-1 - h) / entry - 1.0) for h in horizons}


def liquidity_mask(d: dict[str, pd.DataFrame], min_usd: float = 20e6) -> pd.DataFrame:
    liq = d["qvol"].rolling(30, min_periods=15).median().shift(1)
    return liq >= min_usd


def ic_table(feats: dict[str, pd.DataFrame], fwd: pd.DataFrame, mask: pd.DataFrame,
             split: str, horizon: int) -> pd.DataFrame:
    rows = []
    for name, m in feats.items():
        x = _xs_z(m.where(mask))
        y = fwd.where(mask)
        common = x.index.intersection(y.index)
        x, y = x.loc[common], y.loc[common]
        rx = x.rank(axis=1)
        ry = y.where(x.notna()).rank(axis=1)
        n = (rx.notna() & ry.notna()).sum(axis=1)
        ok = n >= 12
        rx, ry = rx.where(ok), ry.where(ok)
        cx = rx.sub(rx.mean(axis=1), axis=0)
        cy = ry.sub(ry.mean(axis=1), axis=0)
        ic = (cx * cy).sum(axis=1) / np.sqrt((cx ** 2).sum(axis=1) * (cy ** 2).sum(axis=1))
        ic = ic.dropna()
        if len(ic) < 100:
            continue
        a, b = ic[ic.index < pd.Timestamp(split)], ic[ic.index >= pd.Timestamp(split)]

        def st(s):
            if len(s) < 30:
                return np.nan, np.nan
            se = s.std(ddof=1) / np.sqrt(len(s) / max(1, horizon))
            return float(s.mean()), float(s.mean() / se) if se > 0 else np.nan
        ai, at = st(a)
        bi, bt = st(b)
        rows.append(dict(feature=name, ic_is=ai, t_is=at, ic_oos=bi, t_oos=bt,
                         n_is=len(a), n_oos=len(b),
                         same=bool(np.sign(ai) == np.sign(bi))))
    return pd.DataFrame(rows).sort_values("t_is", key=lambda s: -s.abs())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bar", type=int, default=60)
    ap.add_argument("--split", default="2025-04-01")
    ap.add_argument("--horizon", type=int, default=1)
    ap.add_argument("--min-liq", type=float, default=20e6)
    a = ap.parse_args()

    pd.set_option("display.width", 200)
    mats = load(a.bar)
    if not mats:
        raise SystemExit("run panel.py first")
    d = to_daily(mats)
    print(f"daily panel: {d['close'].shape[0]} days x {d['close'].shape[1]} symbols "
          f"{d['close'].index.min().date()} .. {d['close'].index.max().date()}")
    f = features(d)
    fwd = forward_returns(d)[a.horizon]
    mask = liquidity_mask(d, a.min_liq)
    print(f"eligible name-days: {int(mask.sum().sum()):,}   "
          f"avg names/day: {mask.sum(axis=1).mean():.1f}")
    print(f"\nIC vs {a.horizon}-day forward return, split {a.split}")
    print(ic_table(f, fwd, mask, a.split, a.horizon)
          .to_string(index=False, float_format=lambda v: f"{v:,.4f}"))


if __name__ == "__main__":
    main()
