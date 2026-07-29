"""Keyless Hyperliquid funding and perpetual-candle downloader."""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
BASE = "https://api.hyperliquid.xyz/info"
BASKET = [
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "LTC",
    "BNB", "AVAX", "DOT", "TRX", "ETC", "BCH", "FIL",
]


def _interval_ms(interval: str) -> int:
    unit = interval[-1].lower()
    value = int(interval[:-1])
    if unit == "h":
        return value * 3_600_000
    if unit == "m":
        return value * 60_000
    if unit == "d":
        return value * 86_400_000
    raise ValueError(f"Unsupported Hyperliquid candle interval: {interval}")


def _post(payload: dict, retries: int = 5):
    body = json.dumps(payload).encode()
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                BASE,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "trading-backtester-research/1.0",
                },
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    return []


def fetch_funding(
    coin: str, start: str, end: str, refresh: bool = False
) -> pd.DataFrame:
    path = CACHE / f"hyperliquid_funding_{coin.lower()}.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path, parse_dates=["time"], index_col="time")
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    rows = []
    cursor = start_ms
    while cursor <= end_ms:
        page = _post(
            {
                "type": "fundingHistory",
                "coin": coin,
                "startTime": cursor,
                "endTime": end_ms,
            }
        )
        if not page:
            break
        rows.extend(page)
        next_cursor = int(page[-1]["time"]) + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        time.sleep(0.04)
    if not rows:
        return pd.DataFrame(columns=["funding_rate", "premium"])
    frame = pd.DataFrame(rows)
    frame["time"] = pd.to_datetime(frame["time"], unit="ms")
    frame["funding_rate"] = pd.to_numeric(frame["fundingRate"], errors="coerce")
    frame["premium"] = pd.to_numeric(frame["premium"], errors="coerce")
    frame = (
        frame[["time", "funding_rate", "premium"]]
        .dropna()
        .drop_duplicates("time")
        .set_index("time")
        .sort_index()
    )
    frame.to_csv(path)
    return frame


def fetch_candles(
    coin: str, start: str, end: str, interval: str = "4h", refresh: bool = False
) -> pd.DataFrame:
    path = CACHE / f"hyperliquid_perp_{coin.lower()}_{interval}.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path, parse_dates=["time"], index_col="time")
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    rows = []
    interval_ms = _interval_ms(interval)
    cursor = start_ms
    # The endpoint caps responses at 5,000 candles. Request chunks smaller
    # than that so longer future windows cannot truncate silently.
    while cursor <= end_ms:
        chunk_end = min(end_ms, cursor + interval_ms * 4_000 - 1)
        page = _post(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": chunk_end,
                },
            }
        )
        rows.extend(page)
        cursor = chunk_end + 1
        time.sleep(0.04)
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(rows)
    # T is inclusive close time in milliseconds.
    frame["time"] = pd.to_datetime(frame["T"].astype("int64") + 1, unit="ms")
    for source, target in (
        ("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume")
    ):
        frame[target] = pd.to_numeric(frame[source], errors="coerce")
    frame = (
        frame[["time", "open", "high", "low", "close", "volume"]]
        .dropna()
        .drop_duplicates("time")
        .set_index("time")
        .sort_index()
    )
    frame.to_csv(path)
    return frame


def download_basket(
    coins: list[str],
    start: str = "2024-04-01",
    end: str = "2026-07-28",
    interval: str = "4h",
    refresh: bool = False,
) -> None:
    for coin in coins:
        try:
            funding = fetch_funding(coin, start, end, refresh)
            candles = fetch_candles(coin, start, end, interval, refresh)
            print(
                f"  {coin}: funding={len(funding)} candles={len(candles)} "
                f"{funding.index.min() if len(funding) else '-'}.."
                f"{funding.index.max() if len(funding) else '-'}"
            )
        except Exception as exc:
            print(f"  {coin}: FAIL {exc!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coins", default=",".join(BASKET))
    parser.add_argument("--start", default="2024-04-01")
    parser.add_argument("--end", default="2026-07-28")
    parser.add_argument("--interval", default="4h")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    download_basket(
        [coin.strip().upper() for coin in args.coins.split(",") if coin.strip()],
        args.start,
        args.end,
        args.interval,
        args.refresh,
    )


if __name__ == "__main__":
    main()
