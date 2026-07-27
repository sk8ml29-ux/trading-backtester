"""
Signal construction, plus the controls that can kill it.

The economic hypothesis has two halves, both about forced rather than informed
flow:

  LOADED     One side of the book is both crowded and underwater. Its exit is
             not a choice, so as long as open interest is holding up, more
             forced flow is still queued in that direction.

  FLUSHED    Once open interest is collapsing, the forced flow is being spent.
             Price was pushed by liquidity demand rather than information, so
             it snaps back once the seller of last resort is done.

The same state variable drives both; only the sign of the open-interest
velocity switches between them.

Controls matter more than the hypothesis here. The trapped-leverage index is a
flow-weighted average of past prices, so it necessarily resembles a moving-
average oscillator. If a plain time-weighted oscillator with the same effective
memory explains the returns just as well, the reconstruction adds nothing and
should be reported as a failure. Those controls are built here alongside the
real features so the comparison is unavoidable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .panel import cross_sectional_z, load_klines

BASE_FEATURES = ["tli", "frac_uw", "disp", "fuel_dn", "fuel_up", "oi_vel",
                 "acct_ls", "tt_pos_ls", "taker_ls"]

CONTROL_LOOKBACKS = (24, 72, 168, 336)


def add_controls(panel: pd.DataFrame) -> pd.DataFrame:
    """Price-history controls, and the overhang signal defined against them.

    OVERHANG is the payload of the whole exercise:

        overhang = ln(cost_basis) - ln(SMA_n(price))

    A moving average says what the price typically was. The reconstructed cost
    basis says what the crowd actually paid, because it weights each past price
    by the position flow that went through it rather than by elapsed time. When
    the crowd paid more than the price typically was, positions were piled on
    into spikes and the book carries an overhang of underwater longs.

    That difference is invisible to any pure price series. It is the one number
    here that a moving average cannot reproduce, which is why it is the signal
    and the moving averages are the control.
    """
    out = []
    for sym, g in panel.groupby("symbol", sort=False):
        kl = load_klines(sym)
        g = g.copy()
        if kl.empty:
            for n in CONTROL_LOOKBACKS:
                g[f"ma_osc_{n}"] = np.nan
                g[f"mom_{n}"] = np.nan
                g[f"rvol_{n}"] = np.nan
                g[f"overhang_{n}"] = np.nan
                g[f"overhang_v{n}"] = np.nan
            out.append(g)
            continue
        c = kl.set_index("time")["close"].astype(float)
        idx = pd.DatetimeIndex(g["time"])
        ln_cb = np.log(g["cost_basis"].to_numpy(float))
        lr = np.log(c).diff()
        # Realised volatility is the obvious alternative explanation for the
        # book-dispersion feature, so it has to be a control too.
        for n in CONTROL_LOOKBACKS:
            g[f"rvol_{n}"] = lr.rolling(n, min_periods=n // 2).std().reindex(idx).to_numpy()
        for n in CONTROL_LOOKBACKS:
            ma = c.rolling(n, min_periods=n // 2).mean()
            g[f"ma_osc_{n}"] = (c / ma - 1.0).reindex(idx).to_numpy()
            g[f"mom_{n}"] = (c / c.shift(n) - 1.0).reindex(idx).to_numpy()
            ln_ma = np.log(ma).reindex(idx).to_numpy()
            oh = ln_cb - ln_ma
            g[f"overhang_{n}"] = oh
            sd = lr.rolling(n, min_periods=n // 2).std().reindex(idx).to_numpy()
            with np.errstate(divide="ignore", invalid="ignore"):
                g[f"overhang_v{n}"] = np.where(sd > 0, oh / (sd * np.sqrt(n)), np.nan)
        out.append(g)
    return pd.concat(out, ignore_index=True).sort_values(["time", "symbol"]).reset_index(drop=True)


def add_derived(panel: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional scores and the trapped-leverage interactions."""
    cols = BASE_FEATURES + [f"ma_osc_{n}" for n in CONTROL_LOOKBACKS] \
        + [f"mom_{n}" for n in CONTROL_LOOKBACKS] \
        + [f"rvol_{n}" for n in CONTROL_LOOKBACKS] \
        + [f"overhang_{n}" for n in CONTROL_LOOKBACKS] \
        + [f"overhang_v{n}" for n in CONTROL_LOOKBACKS]
    p = cross_sectional_z(panel, [c for c in cols if c in panel.columns])
    if p.empty:
        return p

    # Crowding: which side the account base is leaning on. Positive means the
    # long side is the crowded one.
    p["z_crowd"] = p.get("z_acct_ls", 0.0)
    p["z_crowd_pos"] = p.get("z_tt_pos_ls", 0.0)

    # Trapped-ness: the crowded side is underwater. Positive for either
    # "longs crowded and losing" or "shorts crowded and losing".
    p["trap"] = -p["z_tli"] * p["z_crowd"]
    p["trap_dir"] = np.sign(p["z_crowd"])
    p["trap_mag"] = p["trap"].clip(lower=0.0)

    # Phase. Negative open-interest velocity means the forced flow is being
    # spent right now.
    v = p.get("z_oi_vel", pd.Series(0.0, index=p.index))
    p["phase"] = np.tanh(v)

    # LOADED: lean with the forced flow while the book is still full.
    p["gb_loaded"] = -p["trap_dir"] * p["trap_mag"]
    # FLUSHED: fade the forced flow once open interest is collapsing.
    p["gb_flushed"] = p["trap_dir"] * p["trap_mag"] * (-p["phase"]).clip(lower=0.0)
    # Combined state machine: one continuous score, no thresholds.
    p["gb_core"] = -p["trap_dir"] * p["trap_mag"] * p["phase"]

    # Liquidation fuel asymmetry: more ignitable longs below than shorts above.
    p["fuel_net"] = p.get("z_fuel_dn", 0.0) - p.get("z_fuel_up", 0.0)

    # Book concentration amplifies everything: a tight book margin-calls at once.
    p["gb_core_tight"] = p["gb_core"] * (1.0 + 0.5 * (-p.get("z_disp", 0.0)))

    p["uw_skew"] = -p.get("z_frac_uw", 0.0)

    # A high overhang means the book was loaded above the typical price, so the
    # tradeable direction is short. Flip the sign once, here, so every score in
    # the study reads "higher is more bullish".
    for n in CONTROL_LOOKBACKS:
        if f"z_overhang_{n}" in p.columns:
            p[f"gb_oh_{n}"] = -p[f"z_overhang_{n}"]
        if f"z_overhang_v{n}" in p.columns:
            p[f"gb_ohv_{n}"] = -p[f"z_overhang_v{n}"]
    return p


HYPOTHESIS_COLS = ["z_tli", "z_frac_uw", "z_disp", "z_fuel_dn", "z_fuel_up",
                   "z_oi_vel", "z_acct_ls", "z_tt_pos_ls", "z_taker_ls",
                   "trap", "trap_mag", "gb_loaded", "gb_flushed", "gb_core",
                   "gb_core_tight", "fuel_net", "uw_skew"] \
    + [f"gb_oh_{n}" for n in CONTROL_LOOKBACKS] \
    + [f"gb_ohv_{n}" for n in CONTROL_LOOKBACKS]

CONTROL_COLS = [f"z_ma_osc_{n}" for n in CONTROL_LOOKBACKS] \
    + [f"z_mom_{n}" for n in CONTROL_LOOKBACKS] \
    + [f"z_rvol_{n}" for n in CONTROL_LOOKBACKS]


def residualise(panel: pd.DataFrame, target: str, controls: list[str]) -> pd.Series:
    """Strip the part of `target` that plain price history already explains.

    A separate cross-sectional regression per timestamp, so what survives is the
    component of the signal that the position-map reconstruction contributed and
    no moving average could have produced.
    """
    ctrl = [c for c in controls if c in panel.columns]
    if not ctrl:
        return panel[target]

    order = np.argsort(panel["time"].to_numpy(), kind="stable")
    y_all = panel[target].to_numpy(float)[order]
    X_all = panel[ctrl].to_numpy(float)[order]
    t_all = panel["time"].to_numpy()[order]

    resid = np.full(len(y_all), np.nan)
    bounds = np.flatnonzero(np.r_[True, t_all[1:] != t_all[:-1], True])
    k = len(ctrl)
    for a, b in zip(bounds[:-1], bounds[1:]):
        y = y_all[a:b]
        X = X_all[a:b]
        m = np.isfinite(y) & np.isfinite(X).all(axis=1)
        if m.sum() < k + 10:
            continue
        A = np.column_stack([np.ones(m.sum()), X[m]])
        try:
            coef, *_ = np.linalg.lstsq(A, y[m], rcond=None)
        except np.linalg.LinAlgError:
            continue
        idx = np.flatnonzero(m) + a
        resid[idx] = y[m] - A @ coef

    out = np.empty(len(resid))
    out[order] = resid
    return pd.Series(out, index=panel.index)
