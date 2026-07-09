"""Data provider abstraction — swap Yahoo/Binance/CSV without touching strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    max_intraday_days: dict[str, int | None]  # None = unlimited / years


class DataProvider(ABC):
    name: str = "base"

    @abstractmethod
    def supports(self, symbol: str, timeframe: str) -> bool:
        ...

    @abstractmethod
    def fetch(
        self,
        symbol: str,
        timeframe: str,
        start: str,
        end: str | None = None,
    ) -> pd.DataFrame:
        ...

    def intraday_limit_days(self, timeframe: str) -> int | None:
        """None means no practical limit for our use."""
        return self.info().max_intraday_days.get(timeframe)

    @abstractmethod
    def info(self) -> ProviderInfo:
        ...
