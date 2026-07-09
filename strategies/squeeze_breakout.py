from __future__ import annotations

import pandas as pd

from backtest.indicators import add_atr, add_bb_width_percentile, add_bollinger, swing_low
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class SqueezeBreakoutStrategy(Strategy):
    """
    Volatility squeeze breakout: Bollinger width in bottom percentile, then
    close breaks above upper band. Works across forex, crypto, indices.
    """

    name = "squeeze_breakout"

    def __init__(self, config: BacktestConfig):
        self.reward_risk = config.reward_risk
        self.bb_period = config.squeeze_bb_period
        self.width_pct_max = config.squeeze_width_pct_max
        self.width_lookback = config.squeeze_width_lookback
        self.swing_lookback = config.swing_lookback

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        data = add_bollinger(df, period=self.bb_period)
        data = add_bb_width_percentile(data, lookback=self.width_lookback)
        data = add_atr(data, 14)
        data["swing_low"] = swing_low(data["low"], lookback=self.swing_lookback)
        data["was_squeeze"] = data["bb_width_pct"].shift(1) <= self.width_pct_max
        return data

    def allows_regime(self, regime: str) -> bool:
        return regime != "trend_down"

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = ["bb_upper", "bb_mid", "was_squeeze", "ema_slow", "swing_low", "close"]
        if any(pd.isna(row[c]) for c in required):
            return None

        close = float(row["close"])
        if close <= float(row["ema_slow"]):
            return None
        if not bool(row["was_squeeze"]):
            return None
        if close <= float(prev["bb_upper"]):
            return None
        if close <= float(row["bb_upper"]):
            return None

        stop = min(float(row["swing_low"]), float(row["bb_mid"]))
        if stop >= close:
            stop = close - float(row.get("atr", close * 0.01)) * 1.5
        risk = close - stop
        if risk <= 0:
            return None

        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=close + risk * self.reward_risk,
            reason="squeeze_breakout",
        )
