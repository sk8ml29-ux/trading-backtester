"""Binance perpetual futures funding rates — free, no API key required."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backtest.http_client import fetch_json

FUNDING_SYMBOLS: dict[str, str] = {
    "BTC-USD":  "BTCUSDT",
    "ETH-USD":  "ETHUSDT",
    "SOL-USD":  "SOLUSDT",
    "XRP-USD":  "XRPUSDT",
    "BNB-USD":  "BNBUSDT",
    "NEAR-USD": "NEARUSDT",
    "ATOM-USD": "ATOMUSDT",
    "LINK-USD": "LINKUSDT",
    "AVAX-USD": "AVAXUSDT",
    "MATIC-USD":"MATICUSDT",
    "LTC-USD":  "LTCUSDT",
    "DOT-USD":  "DOTUSDT",
    "ADA-USD":  "ADAUSDT",
    "DOGE-USD": "DOGEUSDT",
}

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"


def _cache_path(symbol: str) -> Path:
    safe = symbol.lower().replace("-", "_")
    return CACHE_DIR / f"funding_{safe}.csv"


def fetch_funding_rates(
    symbol: str,
    start: str = "2023-01-01",
    refresh: bool = False,
) -> pd.DataFrame:
    """
    Returnerar DataFrame med kolumner: funding_time (index, UTC), funding_rate.
    Cachar lokalt. Uppdaterar automatiskt om data är gammal.
    """
    pair = FUNDING_SYMBOLS.get(symbol)
    if not pair:
        return pd.DataFrame(columns=["funding_rate"])

    cache = _cache_path(symbol)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    cached_df = None
    if cache.exists() and not refresh:
        cached_df = pd.read_csv(cache, index_col=0, parse_dates=True)
        cached_df.index = pd.to_datetime(cached_df.index, utc=True).tz_localize(None)
        # om cache täcker till nyligen — returnera direkt
        if not cached_df.empty:
            last = cached_df.index[-1]
            hours_old = (pd.Timestamp.now() - last).total_seconds() / 3600
            if hours_old < 9:  # funding betalas var 8:e timme
                print(f"Using cached funding rates [{symbol}]")
                return cached_df

    # Hämta från Binance
    start_ms = _to_ms(start)
    end_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

    # Om cache finns — börja från sista cacheade punkt
    if cached_df is not None and not cached_df.empty:
        start_ms = int(cached_df.index[-1].timestamp() * 1000) + 1

    rows: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        url = (
            f"https://fapi.binance.com/fapi/v1/fundingRate"
            f"?symbol={pair}&limit=1000&startTime={cursor}"
        )
        batch = fetch_json(url)
        if not batch:
            break
        rows.extend(batch)
        last_t = int(batch[-1]["fundingTime"])
        cursor = last_t + 1
        if len(batch) < 1000:
            break
        time.sleep(0.1)

    if not rows and cached_df is not None:
        return cached_df

    if not rows:
        return pd.DataFrame(columns=["funding_rate"])

    new_df = pd.DataFrame(rows)
    new_df["funding_time"] = pd.to_datetime(new_df["fundingTime"], unit="ms", utc=True)
    new_df = new_df.set_index("funding_time")
    new_df.index = new_df.index.tz_localize(None)
    new_df["funding_rate"] = pd.to_numeric(new_df["fundingRate"], errors="coerce")
    new_df = new_df[["funding_rate"]].sort_index()

    if cached_df is not None and not cached_df.empty:
        combined = pd.concat([cached_df, new_df]).sort_index()
        combined = combined[~combined.index.duplicated(keep="last")]
    else:
        combined = new_df

    combined.to_csv(cache)
    return combined


def align_funding_to_bars(
    price_df: pd.DataFrame,
    funding_df: pd.DataFrame,
) -> pd.Series:
    """
    Forward-fill funding rates till varje prisbar.
    Returnerar Series med samma index som price_df.
    """
    if funding_df.empty:
        return pd.Series(0.0, index=price_df.index, name="funding_rate")

    # Reindex till prisbarernas tidsstämplar, forward-fill
    aligned = (
        funding_df["funding_rate"]
        .reindex(price_df.index.union(funding_df.index))
        .sort_index()
        .ffill()
        .reindex(price_df.index)
    )
    return aligned.fillna(0.0)


def _to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
