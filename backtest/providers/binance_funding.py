"""Binance perpetual futures funding rates — free, no API key required.

``fapi.binance.com`` geo-blocks some cloud/VPS IPs (HTTP 451). When that
happens we fall back to OKX public funding-rate history for the same USDT
perp (rates are venue-specific but correlated enough for paper/signal use;
live execution must still use the venue you trade on).
"""

from __future__ import annotations

import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backtest.http_client import HttpGeoBlocked, fetch_json

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
_BINANCE_FAPI = "https://fapi.binance.com/fapi/v1/fundingRate"
_OKX_FUNDING = "https://www.okx.com/api/v5/public/funding-rate-history"


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

    # Hämta från Binance (eller OKX om fapi är geo-blockad)
    start_ms = _to_ms(start)
    end_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

    # Om cache finns — börja från sista cacheade punkt
    if cached_df is not None and not cached_df.empty:
        start_ms = int(cached_df.index[-1].timestamp() * 1000) + 1

    new_df = _fetch_binance_funding(pair, start_ms, end_ms)
    source = "binance_fapi"
    if new_df is None:
        print(f"Binance fapi funding blocked/unavailable for {symbol} — falling back to OKX")
        new_df = _fetch_okx_funding(pair, start_ms)
        source = "okx"

    if (new_df is None or new_df.empty) and cached_df is not None:
        return cached_df

    if new_df is None or new_df.empty:
        return pd.DataFrame(columns=["funding_rate"])

    if cached_df is not None and not cached_df.empty:
        combined = pd.concat([cached_df, new_df]).sort_index()
        combined = combined[~combined.index.duplicated(keep="last")]
    else:
        combined = new_df

    combined.to_csv(cache)
    print(f"Cached funding rates [{symbol}] via {source}: {len(combined)} rows")
    return combined


def _fetch_binance_funding(pair: str, start_ms: int, end_ms: int) -> pd.DataFrame | None:
    rows: list[dict] = []
    cursor = start_ms
    try:
        while cursor < end_ms:
            url = f"{_BINANCE_FAPI}?symbol={pair}&limit=1000&startTime={cursor}"
            batch = fetch_json(url)
            if not batch:
                break
            rows.extend(batch)
            last_t = int(batch[-1]["fundingTime"])
            cursor = last_t + 1
            if len(batch) < 1000:
                break
            time.sleep(0.1)
    except (HttpGeoBlocked, urllib.error.HTTPError, RuntimeError, OSError) as exc:
        print(f"Binance funding fetch failed ({type(exc).__name__}): {exc}")
        return None

    if not rows:
        return pd.DataFrame(columns=["funding_rate"])

    new_df = pd.DataFrame(rows)
    new_df["funding_time"] = pd.to_datetime(new_df["fundingTime"], unit="ms", utc=True)
    new_df = new_df.set_index("funding_time")
    new_df.index = new_df.index.tz_localize(None)
    new_df["funding_rate"] = pd.to_numeric(new_df["fundingRate"], errors="coerce")
    return new_df[["funding_rate"]].dropna().sort_index()


def _fetch_okx_funding(pair: str, start_ms: int) -> pd.DataFrame:
    """OKX public funding history for the matching USDT perpetual."""
    coin = pair.replace("USDT", "")
    inst = f"{coin}-USDT-SWAP"
    rows: list[dict] = []
    after = ""
    try:
        while True:
            url = f"{_OKX_FUNDING}?instId={inst}&limit=100"
            if after:
                url += f"&after={after}"
            payload = fetch_json(url)
            data = payload.get("data", []) if isinstance(payload, dict) else []
            if not data:
                break
            rows.extend(data)
            oldest = int(data[-1]["fundingTime"])
            after = str(oldest)
            if oldest <= start_ms or len(data) < 100:
                break
            time.sleep(0.08)
    except Exception as exc:  # noqa: BLE001
        print(f"OKX funding fetch failed ({type(exc).__name__}): {exc}")
        return pd.DataFrame(columns=["funding_rate"])

    if not rows:
        return pd.DataFrame(columns=["funding_rate"])

    df = pd.DataFrame(rows)
    df["funding_time"] = pd.to_datetime(df["fundingTime"].astype("int64"), unit="ms", utc=True)
    df = df.set_index("funding_time")
    df.index = df.index.tz_localize(None)
    rate = pd.to_numeric(df.get("realizedRate"), errors="coerce")
    rate = rate.fillna(pd.to_numeric(df["fundingRate"], errors="coerce"))
    df["funding_rate"] = rate
    out = df[["funding_rate"]].dropna().sort_index()
    # Keep only rows at/after the requested start (OKX pages newest-first).
    start_ts = pd.Timestamp(start_ms, unit="ms")
    return out[out.index >= start_ts]


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
