"""Forex OHLCV from cached Dukascopy 15m (resample to 30m/1h)."""

from __future__ import annotations

import pandas as pd

from backtest.data_loader import _filter_dates, _load_csv, cache_path, fetch_ohlcv

DEFAULT_START = "2023-01-01"


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe == "15m":
        return df
    rule = "30min" if timeframe == "30m" else "1h"
    ohlc = df.resample(rule, label="left", closed="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })
    return ohlc.dropna(subset=["open"])


def load_forex_entry(
    symbol: str,
    timeframe: str,
    start: str = DEFAULT_START,
    refresh: bool = False,
) -> pd.DataFrame:
    """Prefer 15m Dukascopy cache resampled to 30m/1h for full history."""
    path_15m = cache_path(symbol, "15m", "dukascopy")
    if path_15m.exists() and not refresh:
        df_15m = _filter_dates(_load_csv(str(path_15m)), start, None)
        if len(df_15m) >= 300:
            return resample_ohlcv(df_15m, timeframe) if timeframe != "15m" else df_15m

    tf_native = "15m" if timeframe in ("15m", "30m", "1h") else timeframe
    df = fetch_ohlcv(symbol, tf_native, start=start, refresh=refresh)
    if timeframe != tf_native:
        return resample_ohlcv(df, timeframe)
    return df


def load_forex_regime(symbol: str, start: str = "2020-01-01") -> pd.DataFrame:
    return fetch_ohlcv(symbol, "1d", start=start, refresh=False, provider="yahoo")


def is_forex_symbol(symbol: str) -> bool:
    return symbol.endswith("=X")
