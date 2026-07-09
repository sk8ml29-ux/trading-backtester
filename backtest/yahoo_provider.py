from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import pandas as pd

from backtest.http_client import fetch_json

Interval = Literal["1d", "1h", "30m", "15m", "5m"]

# Yahoo intraday: use range= instead of old start dates
INTRADAY_RANGE: dict[str, str] = {
    "30m": "60d",
    "15m": "60d",
    "5m": "7d",
    "1h": "730d",
}


def yahoo_chart_to_ohlcv(
    symbol: str,
    interval: Interval = "1d",
    start: str | None = "2015-01-01",
    end: str | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV from Yahoo Finance v8 chart API."""
    if interval in INTRADAY_RANGE:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?interval={interval}&range={INTRADAY_RANGE[interval]}"
        )
    else:
        period1 = _to_unix(start) if start else 0
        period2 = _to_unix(end) if end else int(datetime.now(tz=timezone.utc).timestamp())
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?interval={interval}&period1={period1}&period2={period2}"
        )

    payload = fetch_json(url)
    results = payload.get("chart", {}).get("result")
    if not results:
        raise ValueError(f"No chart data for {symbol}")

    block = results[0]
    timestamps = block.get("timestamp") or []
    quote = (block.get("indicators") or {}).get("quote", [{}])[0]

    if not timestamps:
        raise ValueError(f"Empty timestamps for {symbol}")

    df = pd.DataFrame(
        {
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
            "volume": quote.get("volume"),
        },
        index=pd.to_datetime(timestamps, unit="s", utc=True),
    )
    df.index = df.index.tz_localize(None)
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0)
    return df.sort_index()


def _to_unix(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())
