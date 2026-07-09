from __future__ import annotations

import numpy as np
import pandas as pd
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.momentum import RSIIndicator


def add_regime_columns(
    df: pd.DataFrame,
    ema_fast: int = 9,
    ema_slow: int = 200,
    adx_period: int = 14,
    adx_trend_threshold: float = 25.0,
) -> pd.DataFrame:
    """
  Classify regime (Trading Rush inspired daily filter):
  - trend_up: price above fast EMA and fast EMA rising
  - trend_down: price below fast EMA and fast EMA falling
  - range: everything else
    ADX threshold only used as soft filter when adx_trend_threshold > 0.
    """
    out = df.copy()
    out["ema_fast"] = EMAIndicator(close=out["close"], window=ema_fast).ema_indicator()
    out["ema_slow"] = EMAIndicator(close=out["close"], window=ema_slow).ema_indicator()
    out["adx"] = ADXIndicator(
        high=out["high"], low=out["low"], close=out["close"], window=adx_period
    ).adx()

    slope = out["ema_fast"].diff()
    above_fast = out["close"] > out["ema_fast"]
    below_fast = out["close"] < out["ema_fast"]

    out["regime"] = "range"
    trend_up = above_fast & (slope > 0)
    trend_down = below_fast & (slope < 0)

    if adx_trend_threshold > 0:
        strong = out["adx"] >= adx_trend_threshold
        trend_up = trend_up & strong
        trend_down = trend_down & strong

    out.loc[trend_up, "regime"] = "trend_up"
    out.loc[trend_down, "regime"] = "trend_down"

    return out


def add_macd(df: pd.DataFrame) -> pd.DataFrame:
    macd = MACD(close=df["close"], window_slow=26, window_fast=12, window_sign=9)
    out = df.copy()
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_hist"] = macd.macd_diff()
    return out


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    out = df.copy()
    out["rsi"] = RSIIndicator(close=out["close"], window=period).rsi()
    return out


def add_donchian(df: pd.DataFrame, entry_period: int = 20, exit_period: int = 10) -> pd.DataFrame:
    out = df.copy()
    out["donchian_high"] = out["high"].shift(1).rolling(entry_period).max()
    out["donchian_low"] = out["low"].shift(1).rolling(exit_period).min()
    out["donchian_entry_low"] = out["low"].shift(1).rolling(entry_period).min()
    return out


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    out = df.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(period).mean()
    return out


def swing_low(low: pd.Series, lookback: int = 5) -> pd.Series:
    return low.rolling(lookback, min_periods=lookback).min().shift(1)


def swing_high(high: pd.Series, lookback: int = 5) -> pd.Series:
    return high.rolling(lookback, min_periods=lookback).max().shift(1)


def add_ema_stack(df: pd.DataFrame, periods: tuple[int, int, int] = (9, 21, 50)) -> pd.DataFrame:
    out = df.copy()
    for p in periods:
        out[f"ema_{p}"] = EMAIndicator(close=out["close"], window=p).ema_indicator()
    return out


def add_bollinger(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    out = df.copy()
    mid = out["close"].rolling(period).mean()
    dev = out["close"].rolling(period).std()
    out["bb_mid"] = mid
    out["bb_upper"] = mid + std * dev
    out["bb_lower"] = mid - std * dev
    out["bb_width"] = (out["bb_upper"] - out["bb_lower"]) / mid
    return out


def add_bb_width_percentile(df: pd.DataFrame, lookback: int = 100) -> pd.DataFrame:
    out = df.copy()
    if "bb_width" not in out.columns:
        out = add_bollinger(out)
    out["bb_width_pct"] = out["bb_width"].rolling(lookback, min_periods=20).apply(
        lambda x: float(pd.Series(x).rank(pct=True).iloc[-1]), raw=False
    )
    return out
