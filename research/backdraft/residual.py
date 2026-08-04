"""
Residual reversion, filtered by why the move happened.

Large-cap crypto is one asset with many tickers: pairwise correlations sit
around 0.8, so almost all of a coin's move is the market's move. Strip that out
with a rolling beta and what is left, the residual, is small, mean-reverting,
and uncorrelated with the direction of the market.

Plain residual reversion is an old idea and it has one well-known failure mode:
it fades everything, including moves that happened because something genuinely
changed. Fading real news is how these strategies die.

The addition here is a classifier for *why* a residual move happened, built
from two instruments that only exist in derivatives markets and that are
published free:

  ORDER FLOW    the taker-buy split says how much of the bar's volume demanded
                immediate liquidity. A residual move made of one-sided
                aggressive prints is somebody needing out, not somebody who
                knows something.

  BASIS         the premium index prices the perpetual against spot. Real news
                repriced both; a squeeze dislocates the perp alone.

Moves that score high on both are liquidity events and get faded hard. Moves
with quiet flow and an intact basis are treated as information and left alone.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .panel import load


def market_factor(ret: pd.DataFrame, min_names: int = 8) -> pd.Series:
    """Robust market return: the cross-sectional median of the universe."""
    m = ret.median(axis=1)
    return m.where(ret.notna().sum(axis=1) >= min_names)


def rolling_beta(ret: pd.DataFrame, mkt: pd.Series, window: int) -> pd.DataFrame:
    """Trailing beta of each coin to the market, shifted to stay causal."""
    m = mkt.reindex(ret.index)
    cov = ret.mul(m, axis=0).rolling(window, min_periods=window // 3).mean() \
        - ret.rolling(window, min_periods=window // 3).mean().mul(
            m.rolling(window, min_periods=window // 3).mean(), axis=0)
    var = m.rolling(window, min_periods=window // 3).var()
    beta = cov.div(var, axis=0)
    return beta.clip(0.0, 3.0).shift(1)


def residuals(ret: pd.DataFrame, beta_window: int = 24 * 14) -> tuple[pd.DataFrame, pd.Series]:
    mkt = market_factor(ret)
    beta = rolling_beta(ret, mkt, beta_window)
    resid = ret.sub(beta.mul(mkt, axis=0))
    return resid, mkt


def _xs_z(df: pd.DataFrame, clip: float = 3.0, min_names: int = 8) -> pd.DataFrame:
    """Cross-sectional rank to normal scores, one row at a time."""
    from scipy.special import erfinv
    n = df.notna().sum(axis=1)
    r = df.rank(axis=1, pct=False)
    u = r.div(n + 1.0, axis=0).clip(1e-6, 1 - 1e-6)
    z = np.sqrt(2.0) * erfinv(2.0 * u - 1.0)
    z = z.where(n.ge(min_names), np.nan)
    return z.clip(-clip, clip)


def build_signal(mats: dict[str, pd.DataFrame], lookback: int = 6,
                 beta_window: int = 24 * 14, vol_window: int = 24 * 14,
                 stress_window: int = 24 * 14) -> dict[str, pd.DataFrame]:
    """Reversion score, the liquidity classifier, and their combination."""
    ret = mats["ret"]
    resid, mkt = residuals(ret, beta_window)

    # --- how far the residual has run ------------------------------------
    cum = resid.rolling(lookback, min_periods=lookback).sum()
    sd = resid.rolling(vol_window, min_periods=vol_window // 3).std().shift(1)
    disloc = cum.div(sd * np.sqrt(lookback))
    rev_raw = -disloc                       # fade the residual move

    # --- was it liquidity or information? --------------------------------
    ofi = mats["ofi"]
    ofi_run = ofi.rolling(lookback, min_periods=lookback).mean()
    # Flow pushing the same way as the move: the hallmark of forced trading.
    flow_agree = -(ofi_run * np.sign(disloc))

    qvol = mats["qvol"]
    vburst = qvol.div(qvol.rolling(stress_window, min_periods=stress_window // 3)
                      .median().shift(1))
    cnt = mats["cnt"]
    cburst = cnt.div(cnt.rolling(stress_window, min_periods=stress_window // 3)
                     .median().shift(1))

    prem = mats["prem"]
    psd = prem.rolling(stress_window, min_periods=stress_window // 3).std().shift(1)
    prem_z = prem.div(psd)
    prem_x = mats["prem_x"]
    prem_x_z = prem_x.div(psd)
    # Basis dislocated in the same direction as the move.
    basis_agree = -(prem_z * np.sign(disloc))

    z_flow = _xs_z(flow_agree)
    z_vol = _xs_z(np.log(vburst.where(vburst > 0)))
    z_cnt = _xs_z(np.log(cburst.where(cburst > 0)))
    z_basis = _xs_z(basis_agree)

    # Liquidity score: one-sided aggressive flow, a burst of small orders and a
    # dislocated basis all pointing the same way.
    liq = (z_flow + z_basis + 0.5 * z_vol + 0.5 * z_cnt) / 3.0

    z_rev = _xs_z(rev_raw)
    z_liq = _xs_z(liq)

    # Fade harder when the move looks like forced flow, and step back when it
    # does not. The shift keeps the multiplier bounded and always positive, so
    # the trade direction is only ever set by the reversion term.
    gate = (1.0 + np.tanh(z_liq)).clip(lower=0.05)
    combo = z_rev * gate

    return dict(resid=resid, mkt=mkt, disloc=disloc, rev=z_rev, liq=z_liq,
                gate=gate, combo=_xs_z(combo), flow=z_flow, basis=z_basis,
                vburst=z_vol, cburst=z_cnt, prem_x_z=prem_x_z)


def load_and_build(bar_min: int = 60, **kw) -> tuple[dict, dict]:
    mats = load(bar_min)
    if not mats:
        raise SystemExit(f"no {bar_min}m matrices cached — run panel.py first")
    return mats, build_signal(mats, **kw)
