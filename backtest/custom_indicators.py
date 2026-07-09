"""
Proprietary composite indicators (not standard TA library wrappers).

KES — Kinetic Equilibrium Score
  Measures momentum turning positive near dynamic equilibrium in an uptrend structure.

ECI — Edge Compression Index
  Measures directional pressure building during volatility compression before expansion.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.indicators import add_atr, add_bollinger, add_regime_columns


def compute_kes(
    df: pd.DataFrame,
    kinetic_span: int = 5,
    equilibrium_ema: int = 21,
    structure_lookback: int = 8,
    atr_period: int = 14,
) -> pd.DataFrame:
    """
    Kinetic Equilibrium Score (KES) — custom 0..1+ composite.

    kinetic:     bounded short-term momentum (price change / ATR)
    equilibrium: reward proximity to fair-value EMA (best near EMA, not extended)
    structure:   higher-low confirmation vs recent swing
    """
    out = add_atr(df, atr_period).copy()
    atr = out["atr"].replace(0, np.nan)
    ema_eq = out["close"].ewm(span=equilibrium_ema, adjust=False).mean()
    out["kes_ema_eq"] = ema_eq

    raw_change = out["close"] - out["close"].shift(kinetic_span)
    kinetic = np.tanh(raw_change / atr)
    eq_dist = (out["close"] - ema_eq).abs() / (atr * 2.0)
    equilibrium = (1.0 - eq_dist.clip(0, 1)).clip(0, 1)

    swing_low = out["low"].rolling(structure_lookback, min_periods=structure_lookback).min().shift(1)
    higher_low = (out["low"] > swing_low).astype(float)
    structure = higher_low * 0.6 + 0.4  # partial credit always, full on HL

    out["kes_raw"] = kinetic * equilibrium * structure
    out["kes"] = out["kes_raw"].ewm(span=3, adjust=False).mean()
    out["kes_signal"] = out["kes"].ewm(span=9, adjust=False).mean()
    out["kes_hist"] = out["kes"] - out["kes_signal"]
    out["kes_kinetic"] = kinetic
    out["kes_equilibrium"] = equilibrium
    out["kes_structure"] = structure
    return out


def compute_eci(
    df: pd.DataFrame,
    bb_period: int = 20,
    compression_pct: float = 0.25,
    pressure_window: int = 10,
    width_lookback: int = 100,
) -> pd.DataFrame:
    """
    Edge Compression Index (ECI) — custom pressure gauge.

    Tracks signed volume pressure during BB compression; fires on release.
    """
    out = add_bollinger(df, bb_period).copy()
    out = add_atr(out, 14)

    def pct_rank(x):
        s = pd.Series(x)
        return float(s.rank(pct=True).iloc[-1])

    out["eci_width_pct"] = out["bb_width"].rolling(width_lookback, min_periods=20).apply(
        pct_rank, raw=False
    )
    out["eci_compressed"] = out["eci_width_pct"].shift(1) <= compression_pct

    direction = np.where(out["close"] >= out["open"], 1.0, -1.0)
    vol = out["volume"].replace(0, np.nan).fillna(1.0)
    signed_vol = direction * vol
    out["eci_pressure"] = (
        signed_vol.rolling(pressure_window, min_periods=pressure_window).sum()
        / vol.rolling(pressure_window, min_periods=pressure_window).sum()
    )
    out["eci"] = out["eci_pressure"] * (1.0 - out["eci_width_pct"].shift(1).fillna(0.5))
    out["eci_smooth"] = out["eci"].ewm(span=5, adjust=False).mean()
    out["eci_break_high"] = out["close"] > out["high"].shift(1).rolling(pressure_window).max()
    return out
