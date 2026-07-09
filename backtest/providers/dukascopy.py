"""Dukascopy tick feed -> OHLCV (forex). Free historical intraday."""

from __future__ import annotations

import io
import lzma
import struct
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from backtest.providers.base import DataProvider, ProviderInfo

# Yahoo ticker -> Dukascopy instrument
FOREX_MAP: dict[str, str] = {
    "EURUSD=X": "EURUSD",
    "GBPUSD=X": "GBPUSD",
    "USDJPY=X": "USDJPY",
    "AUDUSD=X": "AUDUSD",
    "USDCAD=X": "USDCAD",
    "USDCHF=X": "USDCHF",
    "NZDUSD=X": "NZDUSD",
    "EURGBP=X": "EURGBP",
    "EURJPY=X": "EURJPY",
}

TF_MINUTES = {"15m": 15, "30m": 30, "1h": 60, "1d": 1440}

# Instrument price scale (Dukascopy bi5 integer -> float)
POINT_SCALE: dict[str, float] = {
    "EURUSD": 100_000.0,
    "GBPUSD": 100_000.0,
    "AUDUSD": 100_000.0,
    "USDCAD": 100_000.0,
    "USDCHF": 100_000.0,
    "NZDUSD": 100_000.0,
    "EURGBP": 100_000.0,
    "USDJPY": 1_000.0,
    "EURJPY": 1_000.0,
}


def _http_get_bytes(url: str) -> bytes | None:
    import urllib.request

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 trading-backtester/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                return None
            return resp.read()
    except Exception:
        return None


def _fetch_hour_ticks(
    instrument: str, dt: datetime, scale: float = 100_000.0,
) -> list[tuple[datetime, float]]:
    """One hour of ticks -> (timestamp, mid_price)."""
    url = (
        "https://datafeed.dukascopy.com/datafeed/"
        f"{instrument}/{dt.year}/{dt.month - 1:02d}/{dt.day:02d}/{dt.hour:02d}h_ticks.bi5"
    )
    raw = _http_get_bytes(url)
    if not raw:
        return []

    try:
        data = lzma.decompress(raw)
    except Exception:
        return []

    ticks: list[tuple[datetime, float]] = []
    hour_start = dt.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    for i in range(0, len(data), 20):
        chunk = data[i : i + 20]
        if len(chunk) < 20:
            break
        ms, ask_i, bid_i, _, _ = struct.unpack(">5i", chunk)
        if ask_i <= 0 or bid_i <= 0:
            continue
        ask = ask_i / scale
        bid = bid_i / scale
        mid = (ask + bid) / 2.0
        ts = hour_start + timedelta(milliseconds=ms)
        ticks.append((ts, mid))
    return ticks


def _ticks_to_ohlcv(ticks: list[tuple[datetime, float]], bar_minutes: int) -> pd.DataFrame:
    if not ticks:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame(ticks, columns=["timestamp", "price"])
    df = df.set_index("timestamp").sort_index()
    rule = f"{bar_minutes}min"
    ohlc = df["price"].resample(rule, label="left", closed="left").ohlc()
    ohlc["volume"] = df["price"].resample(rule).count()
    ohlc = ohlc.dropna(subset=["open"])
    ohlc.columns = ["open", "high", "low", "close", "volume"]
    ohlc.index = ohlc.index.tz_localize(None)
    return ohlc


class DukascopyProvider(DataProvider):
    name = "dukascopy"

    def supports(self, symbol: str, timeframe: str) -> bool:
        return symbol in FOREX_MAP and timeframe in TF_MINUTES

    def info(self) -> ProviderInfo:
        return ProviderInfo(name=self.name, max_intraday_days={tf: None for tf in TF_MINUTES})

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        start: str,
        end: str | None = None,
    ) -> pd.DataFrame:
        instrument = FOREX_MAP[symbol]
        scale = POINT_SCALE.get(instrument, 100_000.0)
        bar_min = TF_MINUTES[timeframe]
        start_dt = datetime.strptime(start[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = (
            datetime.strptime(end[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if end
            else datetime.now(tz=timezone.utc)
        )

        all_ticks: list[tuple[datetime, float]] = []
        cursor = start_dt.replace(minute=0, second=0, microsecond=0)
        total_hours = int((end_dt - cursor).total_seconds() / 3600) + 1
        fetched = 0

        while cursor <= end_dt:
            # Skip weekends roughly (forex closed Sat-Sun UTC)
            if cursor.weekday() == 5:
                cursor += timedelta(days=1)
                continue
            if cursor.weekday() == 6 and cursor.hour < 22:
                cursor += timedelta(hours=1)
                continue

            ticks = _fetch_hour_ticks(instrument, cursor, scale)
            all_ticks.extend(ticks)
            cursor += timedelta(hours=1)
            fetched += 1
            if fetched % 168 == 0:
                pct = min(100, int(fetched / max(total_hours, 1) * 100))
                print(f"  Dukascopy {instrument} 1h: ~{pct}% ({fetched}/{total_hours} hours)")
                time.sleep(0.05)
            if fetched > total_hours + 48:
                break

        return _ticks_to_ohlcv(all_ticks, bar_min)
