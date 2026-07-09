from __future__ import annotations

import pandas as pd

from backtest.indicators import add_macd, swing_low
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class MacdPullbackStrategy(Strategy):
    """
    Trading Rush / Rayner style MACD pullback in uptrends.
    Long when MACD crosses above signal below zero line, price above 200 EMA.
    """

    name = "macd_pullback"

    def __init__(self, config: BacktestConfig):
        self.reward_risk = config.reward_risk
        self.strict_trend = config.macd_strict_trend
        self.swing_lookback = config.swing_lookback
        self.require_below_zero = config.macd_require_below_zero
        self.signal_mode = config.macd_signal_mode

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        data = add_macd(df)
        data["swing_low"] = swing_low(data["low"], lookback=self.swing_lookback)
        return data

    def allows_regime(self, regime: str) -> bool:
        if self.strict_trend:
            return regime == "trend_up"
        return regime != "trend_down"

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = ["macd", "macd_signal", "ema_slow", "swing_low", "close"]
        if any(pd.isna(row[c]) for c in required):
            return None

        if float(row["close"]) <= float(row["ema_slow"]):
            return None

        cross_up = prev["macd"] <= prev["macd_signal"] and row["macd"] > row["macd_signal"]
        below_zero = float(row["macd"]) < 0
        hist_flip = float(prev["macd_hist"]) <= 0 and float(row["macd_hist"]) > 0

        signal_ok = False
        if self.signal_mode == "cross_below_zero":
            signal_ok = cross_up and (not self.require_below_zero or below_zero)
        elif self.signal_mode == "histogram_flip":
            signal_ok = hist_flip
        else:  # either
            signal_ok = (cross_up and (not self.require_below_zero or below_zero)) or hist_flip

        if not signal_ok:
            return None

        entry = float(row["close"])
        stop = float(row["swing_low"])
        if stop >= entry:
            stop = entry * 0.98

        risk = entry - stop
        if risk <= 0:
            return None

        take_profit = entry + risk * self.reward_risk
        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=take_profit,
            reason="macd_cross_below_zero",
        )
