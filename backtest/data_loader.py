from __future__ import annotations

import os
from pathlib import Path

import certifi
import pandas as pd

from backtest.providers.registry import provider_for_symbol

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

SYMBOL_ALIASES = {
    "XAUUSD": "GC=F",
    "GOLD": "GC=F",
}


def cache_path(symbol: str, timeframe: str, provider_name: str | None = None) -> Path:
    safe = symbol.replace("=", "").replace("-", "_").lower()
    if provider_name and provider_name != "yahoo":
        return CACHE_DIR / f"{provider_name}_{safe}_{timeframe}.csv"
    return CACHE_DIR / f"{safe}_{timeframe}.csv"


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "1d",
    start: str = "2015-01-01",
    end: str | None = None,
    csv_path: str | None = None,
    use_cache: bool = True,
    refresh: bool = False,
    provider: str | None = None,
) -> pd.DataFrame:
    """
    Load OHLCV via best available provider (Binance > Dukascopy > Polygon > Yahoo > CSV).
    """
    if csv_path:
        return _load_csv(csv_path)

    symbol = SYMBOL_ALIASES.get(symbol.upper(), symbol)
    prov = provider_for_symbol(symbol, timeframe) if provider is None else _resolve_provider(provider)
    path = cache_path(symbol, timeframe, prov.name)

    if use_cache and path.exists() and not refresh:
        df = _load_csv(str(path))
        filtered = _filter_dates(df, start, end)
        if len(filtered) >= 300:
            print(f"Using cached data [{prov.name}]: {path}")
            return filtered
        print(
            f"Cache too short ({len(filtered)} bars for start={start}), "
            f"re-downloading {symbol} ({timeframe})..."
        )
        if path.exists():
            path.unlink()

    print(f"Downloading {symbol} ({timeframe}) from {prov.name}...")
    df = prov.fetch(symbol, timeframe, start=start, end=end)

    if df.empty:
        raise ValueError(f"No data returned for {symbol} via {prov.name}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _save_csv(df, path)
    print(f"Cached {len(df)} bars [{prov.name}] -> {path}")
    return _filter_dates(df, start, end)


def _resolve_provider(name: str):
    from backtest.providers.registry import get_provider
    return get_provider(name)


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
