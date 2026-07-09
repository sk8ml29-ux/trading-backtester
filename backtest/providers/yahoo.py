from __future__ import annotations

import pandas as pd

from backtest.providers.base import DataProvider, ProviderInfo
from backtest.yahoo_provider import yahoo_chart_to_ohlcv

Interval = str

INTRADAY_LIMITS = {
    "30m": 60,
    "15m": 60,
    "5m": 7,
    "1h": 729,
}


class YahooProvider(DataProvider):
    name = "yahoo"

    def supports(self, symbol: str, timeframe: str) -> bool:
        return timeframe in ("1d", "1h", "30m", "15m", "5m")

    def info(self) -> ProviderInfo:
        return ProviderInfo(name=self.name, max_intraday_days=INTRADAY_LIMITS)

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        start: str,
        end: str | None = None,
    ) -> pd.DataFrame:
        interval = timeframe  # type: ignore[arg-type]
        if timeframe not in INTRADAY_LIMITS and timeframe != "1d":
            raise ValueError(f"Yahoo unsupported timeframe: {timeframe}")
        return yahoo_chart_to_ohlcv(symbol, interval=interval, start=start, end=end)
