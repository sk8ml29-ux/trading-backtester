from __future__ import annotations

import os
from pathlib import Path

import certifi
import pandas as pd

from backtest.yahoo_provider import yahoo_chart_to_ohlcv

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

# yfinance symbol -> cache filename stem
SYMBOL_ALIASES = {
    "XAUUSD": "GC=F",
    "GOLD": "GC=F",
}


def cache_path(symbol: str, timeframe: str) -> Path:
    safe = symbol.replace("=", "").replace("-", "_").lower()
    return CACHE_DIR / f"{safe}_{timeframe}.csv"


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "1d",
    start: str = "2015-01-01",
    end: str | None = None,
    csv_path: str | None = None,
    use_cache: bool = True,
    refresh: bool = False,
) -> pd.DataFrame:
    """
    Load OHLCV data.

    Priority:
    1. Explicit --csv path
    2. Local cache (data/cache/) unless refresh=True
    3. Yahoo Finance chart API download (then cache)
    """
    if csv_path:
        return _load_csv(csv_path)

    symbol = SYMBOL_ALIASES.get(symbol.upper(), symbol)
    path = cache_path(symbol, timeframe)

    if use_cache and path.exists() and not refresh:
        print(f"Using cached data: {path}")
        df = _load_csv(str(path))
        return _filter_dates(df, start, end)

    print(f"Downloading {symbol} ({timeframe}) from Yahoo Finance...")
    interval = _normalize_interval(timeframe)
    df = yahoo_chart_to_ohlcv(symbol, interval=interval, start=start, end=end)

    if df.empty:
        raise ValueError(f"No data returned for {symbol}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _save_csv(df, path)
    print(f"Cached {len(df)} bars to {path}")
    return df


def _normalize_interval(timeframe: str) -> str:
    mapping = {"1d": "1d", "1h": "1h", "30m": "30m", "15m": "15m", "5m": "5m"}
    if timeframe not in mapping:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return mapping[timeframe]


def _filter_dates(df: pd.DataFrame, start: str, end: str | None) -> pd.DataFrame:
    out = df.copy()
    if start:
        out = out[out.index >= pd.Timestamp(start)]
    if end:
        out = out[out.index <= pd.Timestamp(end)]
    return out


def _load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["datetime"], index_col="datetime")
    required = {"open", "high", "low", "close"}
    if not required.issubset(df.columns):
        raise ValueError(f"CSV must contain columns: {required}")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df.sort_index()


def _save_csv(df: pd.DataFrame, path: Path) -> None:
    out = df.copy()
    out.insert(0, "datetime", out.index)
    out.to_csv(path, index=False)
