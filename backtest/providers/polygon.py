"""Polygon.io v2 aggregates — stocks/ETFs with long intraday history."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pandas as pd

from backtest.http_client import USER_AGENT, _ssl_context
from backtest.providers.base import DataProvider, ProviderInfo

STOCK_SYMBOLS: set[str] = {
    "AAPL", "NVDA", "TSLA", "AMZN", "QQQ", "SPY",
    "MSFT", "GOOGL", "META", "AMD", "IWM", "DIA",
    "TLT", "GLD", "SLV", "USO", "XLE", "EEM",
}

TF_PARAMS: dict[str, tuple[int, str]] = {
    "15m": (15, "minute"),
    "30m": (30, "minute"),
    "1h": (1, "hour"),
    "1d": (1, "day"),
}

# Free tier: 5 calls/min — stay under with 13s between requests
_MIN_REQUEST_GAP_SEC = 13.0
_last_request_at = 0.0


def _api_key() -> str | None:
    return os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_KEY")


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < _MIN_REQUEST_GAP_SEC:
        time.sleep(_MIN_REQUEST_GAP_SEC - elapsed)
    _last_request_at = time.monotonic()


def _fetch_polygon_json(url: str, timeout: int = 60, retries: int = 6) -> dict:
    for attempt in range(retries):
        _throttle()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, context=_ssl_context(), timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries - 1:
                wait = _MIN_REQUEST_GAP_SEC * (attempt + 1)
                print(f"  Polygon rate limit — waiting {wait:.0f}s...")
                time.sleep(wait)
                continue
            if exc.code == 429:
                raise RuntimeError(
                    "Polygon rate-limited (HTTP 429). Wait 1–2 min and retry."
                ) from exc
            raise RuntimeError(f"Polygon HTTP {exc.code}: {exc.reason}") from exc
    return {}


class PolygonProvider(DataProvider):
    name = "polygon"

    def supports(self, symbol: str, timeframe: str) -> bool:
        if not _api_key():
            return False
        return symbol in STOCK_SYMBOLS and timeframe in TF_PARAMS

    def info(self) -> ProviderInfo:
        return ProviderInfo(name=self.name, max_intraday_days={tf: None for tf in TF_PARAMS})

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        start: str,
        end: str | None = None,
    ) -> pd.DataFrame:
        key = _api_key()
        if not key:
            raise RuntimeError("POLYGON_API_KEY not set")

        mult, span = TF_PARAMS[timeframe]
        start_d = start[:10]
        end_d = (end or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"))[:10]

        rows: list[dict] = []
        url: str | None = (
            f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/"
            f"{mult}/{span}/{start_d}/{end_d}?adjusted=true&sort=asc&limit=50000&apiKey={key}"
        )
        pages = 0
        while url:
            payload = _fetch_polygon_json(url)
            status = payload.get("status")
            if status not in ("OK", "DELAYED"):
                err = payload.get("error") or payload.get("message") or status
                if rows:
                    break
                raise RuntimeError(f"Polygon error for {symbol} {timeframe}: {err}")
            batch = payload.get("results") or []
            rows.extend(batch)
            pages += 1
            if pages % 3 == 0:
                print(f"  Polygon {symbol} {timeframe}: {len(rows)} bars...")
            nxt = payload.get("next_url")
            if not nxt:
                break
            url = nxt if "apiKey=" in nxt else f"{nxt}&apiKey={key}"

        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(rows)
        df["datetime"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = df.set_index("datetime").sort_index()
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.index = df.index.tz_localize(None)
        out = df[["open", "high", "low", "close", "volume"]].dropna()
        if end:
            out = out[out.index <= pd.Timestamp(end)]
        return out
