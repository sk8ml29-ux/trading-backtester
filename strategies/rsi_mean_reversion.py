from __future__ import annotations

import pandas as pd

from backtest.indicators import add_atr, add_rsi
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class RsiMeanReversionStrategy(Strategy):
    """
    Mean reversion for range markets (Rayner / Connors style).
    Buy when RSI exits oversold; shorter R:R than trend strategies.
    Requires daily ADX filter so weak-trend bars classify as range.
    """

    name = "rsi_mean_reversion"

    def __init__(self, config: BacktestConfig):
        self.rsi_period = config.rsi_period
        self.oversold = config.rsi_oversold
        self.atr_sl = config.rsi_atr_sl
        self.atr_tp = config.rsi_atr_tp

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        data = add_rsi(df, self.rsi_period)
        return add_atr(data, 14)

    def allows_regime(self, regime: str) -> bool:
        return regime == "range"

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = ["rsi", "atr", "close"]
        if any(pd.isna(row[c]) for c in required):
            return None

        cross_up = prev["rsi"] < self.oversold and row["rsi"] >= self.oversold
        if not cross_up:
            return None

        entry = float(row["close"])
        atr = float(row["atr"])
        if atr <= 0:
            return None

        stop = entry - atr * self.atr_sl
        take_profit = entry + atr * self.atr_tp

        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=take_profit,
            reason="rsi_oversold_bounce",
        )
