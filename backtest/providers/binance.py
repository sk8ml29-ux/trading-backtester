"""Binance public klines — years of intraday crypto, no API key required."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd

from backtest.http_client import fetch_json
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

        rows: list[list] = []
        cursor = start_ms
        while cursor < end_ms:
            url = (
                "https://api.binance.com/api/v3/klines?"
                f"symbol={pair}&interval={interval}&limit=1000&startTime={cursor}"
            )
            batch = fetch_json(url)
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
