from __future__ import annotations

import pandas as pd

from backtest.indicators import add_atr, add_ema_stack, swing_low
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class AdaptiveTrendPullbackStrategy(Strategy):
    """
    Cross-asset trend pullback: EMA ribbon aligned (9>21>50), price above 200 EMA,
    buy bullish bounce off 21 EMA in daily trend_up regime.
    """

    name = "adaptive_trend_pullback"

    def __init__(self, config: BacktestConfig):
        self.reward_risk = config.reward_risk
        self.pullback_ema = config.trend_pullback_ema
        self.swing_lookback = config.swing_lookback
        self.strict_trend = config.macd_strict_trend

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        data = add_ema_stack(df, (9, 21, 50))
        data = add_atr(data, 14)
        data["swing_low"] = swing_low(data["low"], lookback=self.swing_lookback)
        return data

    def allows_regime(self, regime: str) -> bool:
        if self.strict_trend:
            return regime == "trend_up"
        return regime != "trend_down"

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        ema_pb = f"ema_{self.pullback_ema}"
        required = [ema_pb, "ema_9", "ema_21", "ema_50", "ema_slow", "swing_low", "close"]
        if any(pd.isna(row[c]) for c in required):
            return None

        close = float(row["close"])
        if close <= float(row["ema_slow"]):
            return None

        e9, e21, e50 = float(row["ema_9"]), float(row["ema_21"]), float(row["ema_50"])
        if not (e9 > e21 > e50):
            return None

        pb = float(row[ema_pb])
        low = float(row["low"])
        open_ = float(row["open"])

        touched = low <= pb * 1.002
        bullish = close > open_ and close > pb
        if not (touched and bullish):
            return None

        stop = float(row["swing_low"])
        if stop >= close:
            stop = close - float(row.get("atr", close * 0.01)) * 1.2
        risk = close - stop
        if risk <= 0:
            return None

        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=close + risk * self.reward_risk,
            reason="ema_pullback_bounce",
        )
