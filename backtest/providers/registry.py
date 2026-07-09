from __future__ import annotations

from backtest.providers.base import DataProvider
from backtest.providers.binance import BinanceProvider
from backtest.providers.dukascopy import DukascopyProvider
from backtest.providers.polygon import PolygonProvider
from backtest.providers.yahoo import YahooProvider

_PROVIDERS: list[DataProvider] = [
    BinanceProvider(),
    DukascopyProvider(),
    PolygonProvider(),
    YahooProvider(),
]


def provider_for_symbol(symbol: str, timeframe: str) -> DataProvider:
    for p in _PROVIDERS:
        if p.supports(symbol, timeframe):
            return p
    return _PROVIDERS[-1]  # Yahoo fallback


def get_provider(name: str) -> DataProvider:
    for p in _PROVIDERS:
        if p.name == name:
            return p
    raise KeyError(name)
