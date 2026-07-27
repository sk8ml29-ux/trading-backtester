"""
Information-coefficient study.

Before any backtest, measure whether the reconstructed state variables carry
cross-sectional predictive content at all, and over what horizon it decays.
The IC is the per-timestamp Spearman correlation between a feature and the
forward return; its mean divided by its standard error is the t-statistic that
decides whether a feature is worth trading.

Everything here reports in-sample and out-of-sample side by side, because a
feature that only works before the split date is noise.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ic_series(panel: pd.DataFrame, feature: str, ret_col: str = "fwd_ret",
              min_names: int = 15) -> pd.Series:
    """Spearman IC per timestamp."""
    sub = panel[["time", feature, ret_col]].dropna()
    if sub.empty:
        return pd.Series(dtype=float)

    def one(g: pd.DataFrame) -> float:
        if len(g) < min_names:
            return np.nan
        a = g[feature].rank()
        b = g[ret_col].rank()
        sa, sb = a.std(), b.std()
        if sa == 0 or sb == 0:
            return np.nan
        return float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))

    return sub.groupby("time")[[feature, ret_col]].apply(one).dropna()


def ic_stats(ic: pd.Series, sample_gap_h: int = 8, horizon_h: int = 8) -> dict:
    """Mean IC with a t-stat corrected for the overlap between samples.

    Consecutive observations overlap when the holding horizon is longer than the
    sampling interval, which inflates a naive t-stat; the Newey-West style
    deflator below keeps it honest.
    """
    if len(ic) < 10:
        return dict(n=len(ic), ic=np.nan, t=np.nan, hit=np.nan)
    overlap = max(1.0, horizon_h / max(sample_gap_h, 1))
    eff_n = len(ic) / overlap
    se = ic.std(ddof=1) / np.sqrt(max(eff_n, 1.0))
    return dict(n=int(len(ic)), ic=float(ic.mean()), t=float(ic.mean() / se) if se > 0 else np.nan,
                hit=float((ic > 0).mean()))


def feature_report(panel: pd.DataFrame, features: list[str], split: str,
                   ret_col: str = "fwd_ret", sample_gap_h: int = 8,
                   horizon_h: int = 8) -> pd.DataFrame:
    """IC table for every feature, split into in-sample and out-of-sample."""
    split_ts = pd.Timestamp(split)
    rows = []
    for f in features:
        if f not in panel.columns:
            continue
        ic = ic_series(panel, f, ret_col)
        if ic.empty:
            continue
        is_ic = ic[ic.index < split_ts]
        oos_ic = ic[ic.index >= split_ts]
        a = ic_stats(is_ic, sample_gap_h, horizon_h)
        b = ic_stats(oos_ic, sample_gap_h, horizon_h)
        rows.append(dict(feature=f, ic_is=a["ic"], t_is=a["t"], n_is=a["n"],
                         ic_oos=b["ic"], t_oos=b["t"], n_oos=b["n"],
                         same_sign=bool(np.sign(a["ic"]) == np.sign(b["ic"]))))
    return pd.DataFrame(rows).sort_values("t_is", key=lambda s: -s.abs())


def decay_report(panel: pd.DataFrame, feature: str, horizons: list[int],
                 sample_gap_h: int = 8) -> pd.DataFrame:
    """How fast the signal's predictive content dies with holding period."""
    rows = []
    for h in horizons:
        col = f"fwd_{h}h"
        if col not in panel.columns:
            continue
        ic = ic_series(panel, feature, col)
        st = ic_stats(ic, sample_gap_h, h)
        rows.append(dict(horizon_h=h, **st))
    return pd.DataFrame(rows)


def quantile_returns(panel: pd.DataFrame, feature: str, q: int = 5,
                     ret_col: str = "fwd_ret") -> pd.DataFrame:
    """Average forward return by feature quantile — checks for monotonicity.

    A real effect should grade smoothly across buckets; a single freak bucket
    usually means a handful of outlier prints.
    """
    sub = panel[["time", "symbol", feature, ret_col]].dropna().copy()
    if sub.empty:
        return pd.DataFrame()
    sub["bucket"] = sub.groupby("time")[feature].transform(
        lambda s: pd.qcut(s.rank(method="first"), q, labels=False, duplicates="drop")
        if s.notna().sum() >= q * 3 else np.nan)
    sub = sub.dropna(subset=["bucket"])
    g = sub.groupby("bucket")[ret_col]
    return pd.DataFrame(dict(mean_ret=g.mean(), n=g.size())).reset_index()
