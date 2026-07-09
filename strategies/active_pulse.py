from __future__ import annotations

import pandas as pd

from backtest.indicators import add_atr, add_rsi
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ActivePulseStrategy(Strategy):
    """
    Higher-frequency trend pullback for 30m.

    Logic: in non-downtrends, buy RSI recovery after dip while fast EMA > slow EMA.
    Fixed ATR stop with configurable R:R (default 1.5) — works with 50%+ win rate.
    """

    name = "active_pulse"

    def __init__(self, config: BacktestConfig):
        self.rsi_period = config.pulse_rsi_period
        self.rsi_buy = config.pulse_rsi_buy
        self.atr_sl = config.pulse_atr_sl
        self.reward_risk = config.pulse_reward_risk
        self.require_above_200 = config.pulse_require_above_200

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        from ta.trend import EMAIndicator

        data = add_rsi(df, self.rsi_period)
        data = add_atr(data, 14)
        data["ema_fast"] = EMAIndicator(close=data["close"], window=9).ema_indicator()
        data["ema_mid"] = EMAIndicator(close=data["close"], window=21).ema_indicator()
        return data

    def allows_regime(self, regime: str) -> bool:
        return regime != "trend_down"

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = ["rsi", "atr", "ema_fast", "ema_mid", "ema_slow", "close", "open"]
        if any(pd.isna(row[c]) for c in required):
            return None

        close = float(row["close"])
        if self.require_above_200 and close <= float(row["ema_slow"]):
            return None
        if float(row["ema_fast"]) <= float(row["ema_mid"]):
            return None

        rsi_cross = float(prev["rsi"]) < self.rsi_buy and float(row["rsi"]) >= self.rsi_buy
        bullish = close > float(row["open"])
        if not (rsi_cross and bullish):
            return None

        atr = float(row["atr"])
        if atr <= 0:
            return None

        stop = close - atr * self.atr_sl
        take_profit = close + atr * self.atr_sl * self.reward_risk
        if stop >= close:
            return None

        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=take_profit,
            reason="pulse_rsi_recovery",
        )
