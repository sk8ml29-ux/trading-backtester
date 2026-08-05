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

# On refresh, re-pull a short overlap so the last few bars can revise, instead of
# re-downloading the entire history from ``start`` (critical for Binance 15m).
_REFRESH_OVERLAP = {
    "1m": pd.Timedelta(hours=6),
    "5m": pd.Timedelta(days=1),
    "15m": pd.Timedelta(days=2),
    "30m": pd.Timedelta(days=3),
    "1h": pd.Timedelta(days=5),
    "4h": pd.Timedelta(days=14),
    "1d": pd.Timedelta(days=30),
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

    if use_cache and path.exists() and refresh:
        updated = _incremental_refresh(prov, path, symbol, timeframe, start, end)
        if updated is not None:
            return updated

    print(f"Downloading {symbol} ({timeframe}) from {prov.name}...")
    df = prov.fetch(symbol, timeframe, start=start, end=end)

    if df.empty:
        raise ValueError(f"No data returned for {symbol} via {prov.name}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _save_csv(df, path)
    print(f"Cached {len(df)} bars [{prov.name}] -> {path}")
    return _filter_dates(df, start, end)


def _incremental_refresh(
    prov,
    path: Path,
    symbol: str,
    timeframe: str,
    start: str,
    end: str | None,
) -> pd.DataFrame | None:
    """Append only recent bars onto an existing cache. Returns None to force full fetch."""
    try:
        cached = _load_csv(str(path))
    except Exception as exc:  # noqa: BLE001
        print(f"Cache unreadable ({path}): {exc} — full re-download")
        return None
    if cached.empty:
        return None

    overlap = _REFRESH_OVERLAP.get(timeframe, pd.Timedelta(days=3))
    refresh_from = (cached.index.max() - overlap).strftime("%Y-%m-%d")
    print(
        f"Refreshing {symbol} ({timeframe}) from {refresh_from} via {prov.name} "
        f"(incremental, cache has {len(cached)} bars)..."
    )
    try:
        fresh = prov.fetch(symbol, timeframe, start=refresh_from, end=end)
    except Exception as exc:  # noqa: BLE001
        print(f"Incremental refresh failed ({type(exc).__name__}: {exc}) — using cache")
        return _filter_dates(cached, start, end)

    if fresh.empty:
        print(f"No new bars for {symbol} ({timeframe}); keeping cache")
        return _filter_dates(cached, start, end)

    combined = pd.concat([cached, fresh]).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _save_csv(combined, path)
    print(f"Updated cache {len(combined)} bars [{prov.name}] -> {path}")
    return _filter_dates(combined, start, end)


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
