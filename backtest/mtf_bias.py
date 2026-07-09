"""Merge higher-timeframe trend bias onto entry bars."""

from __future__ import annotations

import pandas as pd
from ta.trend import EMAIndicator

from backtest.data_loader import fetch_ohlcv
from backtest.mtf import clamp_start_for_timeframe


def compute_bias_series(df: pd.DataFrame, ema_period: int = 21) -> pd.Series:
    """+1 bull, -1 bear, 0 neutral."""
    ema = EMAIndicator(close=df["close"], window=ema_period).ema_indicator()
    slope = ema.diff()
    bias = pd.Series(0, index=df.index, dtype=int)
    bias[(df["close"] > ema) & (slope > 0)] = 1
    bias[(df["close"] < ema) & (slope < 0)] = -1
    return bias


def merge_higher_bias(
    entry: pd.DataFrame,
    symbol: str,
    higher_tf: str,
    col_name: str,
    ema_period: int = 21,
) -> pd.DataFrame:
    try:
        if symbol.endswith("=X"):
            from research.forex_search import load_cached_forex
            hdf = load_cached_forex(symbol, higher_tf)
        else:
            start = clamp_start_for_timeframe("2015-01-01", higher_tf)
            hdf = fetch_ohlcv(symbol, higher_tf, start=start, refresh=False)
    except Exception:
        entry[col_name] = 0
        return entry

    bias = compute_bias_series(hdf, ema_period)
    lookup = bias.rename_axis("timestamp").reset_index()
    lookup.columns = ["timestamp", col_name]

    out = entry.reset_index(names="timestamp")
    out["timestamp"] = pd.to_datetime(out["timestamp"]).astype("datetime64[ns]")
    lookup["timestamp"] = pd.to_datetime(lookup["timestamp"]).astype("datetime64[ns]")

    merged = pd.merge_asof(
        out.sort_values("timestamp"),
        lookup.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    ).set_index("timestamp")

    merged[col_name] = merged[col_name].fillna(0).astype(int)
    return merged


def alignment_score(row: pd.Series, entry_tf: str) -> tuple[int, int, int]:
    """
    Return (long_score, short_score) from 0-3 based on how many TFs agree.
    Max 3 on 15m (1h+30m+self), 2 on 30m, 1 on 1h.
    """
    local = int(row.get("bias_local", 0))
    b30 = int(row.get("bias_30m", 0))
    b1h = int(row.get("bias_1h", 0))

    if entry_tf == "15m":
        long_parts = [b1h, b30, local]
        short_parts = [b1h, b30, local]
    elif entry_tf == "30m":
        long_parts = [b1h, local]
        short_parts = [b1h, local]
    else:  # 1h
        long_parts = [local]
        short_parts = [local]

    long_score = sum(1 for x in long_parts if x >= 1)
    short_score = sum(1 for x in short_parts if x <= -1)
    need = len(long_parts)
    return long_score, short_score, need
