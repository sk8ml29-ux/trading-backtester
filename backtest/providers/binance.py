"""Binance public klines — years of intraday crypto, no API key required.

``api.binance.com`` geo-blocks some cloud/VPS egress IPs with HTTP 451.
The unrestricted market-data mirror ``data-api.binance.vision`` exposes the
same ``/api/v3/klines`` contract and is tried first. Override the host list
with env ``BINANCE_SPOT_API_BASE`` (single base URL, no trailing path).
"""

from __future__ import annotations

import os
import time
import urllib.error
from datetime import datetime, timezone

import pandas as pd

from backtest.http_client import HttpGeoBlocked, fetch_json
from backtest.providers.base import DataProvider, ProviderInfo

# Yahoo-style tickers used in universe -> Binance USDT pair
BINANCE_SYMBOLS: dict[str, str] = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "SOL-USD": "SOLUSDT",
    "XRP-USD": "XRPUSDT",
    "ADA-USD": "ADAUSDT",
    "DOGE-USD": "DOGEUSDT",
    "LINK-USD": "LINKUSDT",
    "AVAX-USD": "AVAXUSDT",
    "BNB-USD": "BNBUSDT",
    "MATIC-USD": "MATICUSDT",
    "LTC-USD": "LTCUSDT",
    "DOT-USD": "DOTUSDT",
    "FIL-USD": "FILUSDT",
    "ATOM-USD": "ATOMUSDT",
    "NEAR-USD": "NEARUSDT",
}

INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}

# Vision first (works from geo-restricted egress); official API as fallback.
_DEFAULT_SPOT_BASES: tuple[str, ...] = (
    "https://data-api.binance.vision",
    "https://api.binance.com",
)


def _spot_bases() -> list[str]:
    env = os.environ.get("BINANCE_SPOT_API_BASE", "").strip()
    if env:
        return [env.rstrip("/")]
    return list(_DEFAULT_SPOT_BASES)


def _klines_url(base: str, pair: str, interval: str, start_ms: int) -> str:
    return (
        f"{base.rstrip('/')}/api/v3/klines?"
        f"symbol={pair}&interval={interval}&limit=1000&startTime={start_ms}"
    )


def _fetch_klines_batch(bases: list[str], pair: str, interval: str, start_ms: int) -> tuple[list, str]:
    """Try each base until one returns a batch. Returns (batch, working_base)."""
    errors: list[str] = []
    for base in bases:
        url = _klines_url(base, pair, interval, start_ms)
        try:
            batch = fetch_json(url)
        except HttpGeoBlocked as exc:
            errors.append(f"{base}: {exc}")
            continue
        except urllib.error.HTTPError as exc:
            errors.append(f"{base}: HTTP {exc.code}")
            continue
        except Exception as exc:  # noqa: BLE001 - try next mirror
            errors.append(f"{base}: {type(exc).__name__}: {exc}")
            continue
        if batch is None:
            errors.append(f"{base}: empty response")
            continue
        return batch, base
    raise RuntimeError(
        "Binance klines unreachable from all spot bases "
        f"({', '.join(bases)}). Last errors: {'; '.join(errors)}"
    )


class BinanceProvider(DataProvider):
    name = "binance"

    def supports(self, symbol: str, timeframe: str) -> bool:
        return symbol in BINANCE_SYMBOLS and timeframe in INTERVAL_MS

    def info(self) -> ProviderInfo:
        # ~years of 15m via pagination
        return ProviderInfo(
            name=self.name,
            max_intraday_days={tf: None for tf in INTERVAL_MS},
        )

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        start: str,
        end: str | None = None,
    ) -> pd.DataFrame:
        pair = BINANCE_SYMBOLS[symbol]
        interval = timeframe
        step = INTERVAL_MS[interval]
        start_ms = _to_ms(start)
        end_ms = _to_ms(end) if end else int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        bases = _spot_bases()

        rows: list[list] = []
        cursor = start_ms
        working_base: str | None = None
        while cursor < end_ms:
            try_bases = [working_base] if working_base else bases
            # If the working host later fails, fall back to the full list.
            try:
                batch, working_base = _fetch_klines_batch(try_bases, pair, interval, cursor)
            except RuntimeError:
                if working_base and try_bases == [working_base]:
                    working_base = None
                    batch, working_base = _fetch_klines_batch(bases, pair, interval, cursor)
                else:
                    raise
            if not batch:
                break
            rows.extend(batch)
            last_open = int(batch[-1][0])
            next_cursor = last_open + step
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(batch) < 1000:
                break
            time.sleep(0.12)  # gentle rate limit

        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(
            rows,
            columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_vol", "trades", "tbb", "tbq", "ignore",
            ],
        )
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.set_index("datetime").sort_index()
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.index = df.index.tz_localize(None)
        out = df[["open", "high", "low", "close", "volume"]].dropna()
        if end:
            out = out[out.index <= pd.Timestamp(end)]
        return out


def _to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
