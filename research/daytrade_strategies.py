"""
Signal generators for the daytrade lab.

Each function takes an OHLCV DataFrame and a parameter dict, computes indicators
(including an ``atr`` column used by the simulator for stops), and returns
``(long_sig, short_sig)`` as boolean numpy arrays aligned to df rows. Signals are
evaluated on the *closed* bar; the simulator opens at the next bar's open.
"""
from __future__ import annotations

import pandas as pd

from research.daytrade_lab import ema, rsi, atr, rolling_z


def _prep_atr(df: pd.DataFrame, period: int) -> None:
    df["atr"] = atr(df, period)


# ---------------------------------------------------------------------------
# 1) Bollinger / RSI mean reversion (range fade)
# ---------------------------------------------------------------------------
def mean_reversion_bb(df: pd.DataFrame, p: dict):
    _prep_atr(df, p["atr_p"])
    close = df["close"]
    period = p["bb_p"]
    std = p["bb_std"]
    mid = close.rolling(period).mean()
    dev = close.rolling(period).std(ddof=0)
    lower = mid - std * dev
    upper = mid + std * dev
    r = rsi(close, p["rsi_p"])
    trend = ema(close, p["trend_ema"])

    long_sig = (close < lower) & (r < p["rsi_os"])
    short_sig = (close > upper) & (r > p["rsi_ob"])
    if p.get("trend_filter", True):
        # only fade with the higher-timeframe drift: buy dips above trend, sell rips below
        long_sig &= close > trend
        short_sig &= close < trend
    return long_sig.to_numpy(bool), short_sig.to_numpy(bool)


# ---------------------------------------------------------------------------
# 2) Z-score reversion (distance from moving average)
# ---------------------------------------------------------------------------
def zscore_reversion(df: pd.DataFrame, p: dict):
    _prep_atr(df, p["atr_p"])
    close = df["close"]
    z = rolling_z(close, p["z_p"])
    trend = ema(close, p["trend_ema"])
    long_sig = z < -p["z_k"]
    short_sig = z > p["z_k"]
    if p.get("trend_filter", True):
        long_sig &= close > trend
        short_sig &= close < trend
    return long_sig.to_numpy(bool), short_sig.to_numpy(bool)


# ---------------------------------------------------------------------------
# 3) RSI-2 pullback in trend (Connors style, long-biased dip buy + short rip)
# ---------------------------------------------------------------------------
def rsi2_pullback(df: pd.DataFrame, p: dict):
    _prep_atr(df, p["atr_p"])
    close = df["close"]
    r2 = rsi(close, p["rsi_p"])
    trend = ema(close, p["trend_ema"])
    long_sig = (r2 < p["rsi_os"]) & (close > trend)
    short_sig = (r2 > p["rsi_ob"]) & (close < trend)
    return long_sig.to_numpy(bool), short_sig.to_numpy(bool)


# ---------------------------------------------------------------------------
# 4) Donchian volatility breakout (momentum)
# ---------------------------------------------------------------------------
def donchian_breakout(df: pd.DataFrame, p: dict):
    _prep_atr(df, p["atr_p"])
    close = df["close"]
    hi = df["high"].shift(1).rolling(p["lookback"]).max()
    lo = df["low"].shift(1).rolling(p["lookback"]).min()
    trend = ema(close, p["trend_ema"])
    long_sig = close > hi
    short_sig = close < lo
    if p.get("trend_filter", True):
        long_sig &= close > trend
        short_sig &= close < trend
    return long_sig.to_numpy(bool), short_sig.to_numpy(bool)


# ---------------------------------------------------------------------------
# 5) Session opening-range breakout style momentum on intraday high/low + EMA
# ---------------------------------------------------------------------------
def ema_pullback_trend(df: pd.DataFrame, p: dict):
    _prep_atr(df, p["atr_p"])
    close = df["close"]
    ef = ema(close, p["ema_fast"])
    es = ema(close, p["ema_slow"])
    r = rsi(close, p["rsi_p"])
    up = es.diff() > 0
    dn = es.diff() < 0
    # buy pullback to fast EMA in uptrend when momentum turns back up
    long_sig = up & (df["low"] <= ef) & (close > ef) & (r > p["rsi_mid"])
    short_sig = dn & (df["high"] >= ef) & (close < ef) & (r < (100 - p["rsi_mid"]))
    return long_sig.to_numpy(bool), short_sig.to_numpy(bool)


STRATS = {
    "mean_reversion_bb": mean_reversion_bb,
    "zscore_reversion": zscore_reversion,
    "rsi2_pullback": rsi2_pullback,
    "donchian_breakout": donchian_breakout,
    "ema_pullback_trend": ema_pullback_trend,
}


# Parameter grids for walk-forward search (kept modest for speed).
GRIDS = {
    "mean_reversion_bb": [
        dict(atr_p=14, bb_p=bb_p, bb_std=bb_std, rsi_p=rsi_p, rsi_os=rsi_os,
             rsi_ob=100 - rsi_os, trend_ema=trend_ema, trend_filter=tf,
             sl_atr=sl, tp_atr=tp, max_hold=mh)
        for bb_p in (20, 30)
        for bb_std in (2.0, 2.5)
        for rsi_p in (7, 14)
        for rsi_os in (10, 20, 30)
        for trend_ema in (100, 200)
        for tf in (True, False)
        for sl in (2.0, 3.0)
        for tp in (2.0, 4.0)
        for mh in (48, 96)
    ],
    "zscore_reversion": [
        dict(atr_p=14, z_p=z_p, z_k=z_k, trend_ema=trend_ema, trend_filter=tf,
             sl_atr=sl, tp_atr=tp, max_hold=mh)
        for z_p in (20, 40)
        for z_k in (2.0, 2.5, 3.0)
        for trend_ema in (100, 200)
        for tf in (True, False)
        for sl in (2.0, 3.0)
        for tp in (2.0, 4.0)
        for mh in (48, 96)
    ],
    "rsi2_pullback": [
        dict(atr_p=14, rsi_p=rsi_p, rsi_os=rsi_os, rsi_ob=100 - rsi_os,
             trend_ema=trend_ema, sl_atr=sl, tp_atr=tp, max_hold=mh)
        for rsi_p in (2, 3, 5)
        for rsi_os in (5, 10, 15)
        for trend_ema in (100, 200)
        for sl in (2.5, 3.5)
        for tp in (2.0, 3.0, 5.0)
        for mh in (48, 96, 192)
    ],
    "donchian_breakout": [
        dict(atr_p=14, lookback=lb, trend_ema=trend_ema, trend_filter=tf,
             sl_atr=sl, tp_atr=tp, max_hold=mh)
        for lb in (20, 40, 60)
        for trend_ema in (100, 200)
        for tf in (True, False)
        for sl in (1.5, 2.5)
        for tp in (2.0, 3.0)
        for mh in (24, 96)
    ],
    "ema_pullback_trend": [
        dict(atr_p=14, ema_fast=ef, ema_slow=es, rsi_p=14, rsi_mid=rmid,
             sl_atr=sl, tp_atr=tp, max_hold=mh)
        for ef in (9, 21)
        for es in (50, 100)
        for rmid in (45, 50, 55)
        for sl in (1.5, 2.5)
        for tp in (1.5, 2.5)
        for mh in (24, 96)
    ],
}
