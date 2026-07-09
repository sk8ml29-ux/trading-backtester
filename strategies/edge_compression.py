from __future__ import annotations

import pandas as pd

from backtest.custom_indicators import compute_eci
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class EdgeCompressionStrategy(Strategy):
    """
    Proprietary ECI (Edge Compression Index) strategy.
    Long on compression release with positive directional pressure and range break.
    """

    name = "edge_compression"

    def __init__(self, config: BacktestConfig):
        self.reward_risk = config.reward_risk
        self.eci_threshold = config.eci_entry_threshold
        self.compression_pct = config.eci_compression_pct
        self.pressure_window = config.eci_pressure_window
        self.bb_period = config.squeeze_bb_period
        self.swing_lookback = config.swing_lookback

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        from backtest.indicators import swing_low

        data = compute_eci(
            df,
            bb_period=self.bb_period,
            compression_pct=self.compression_pct,
            pressure_window=self.pressure_window,
        )
        data["swing_low"] = swing_low(data["low"], lookback=self.swing_lookback)
        return data

    def allows_regime(self, regime: str) -> bool:
        return regime != "trend_down"

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = ["eci_smooth", "eci_compressed", "eci_break_high", "ema_slow", "swing_low", "close"]
        if any(pd.isna(row[c]) for c in required):
            return None

        close = float(row["close"])
        if close <= float(row["ema_slow"]):
            return None
        if not bool(row["eci_compressed"]):
            return None
        if not bool(row["eci_break_high"]):
            return None
        if float(row["eci_smooth"]) < self.eci_threshold:
            return None
        if float(prev["eci_smooth"]) >= self.eci_threshold:
            return None

        stop = min(float(row["swing_low"]), float(row.get("bb_mid", row["swing_low"])))
        if stop >= close:
            stop = close - float(row.get("atr", close * 0.01)) * 1.5
        risk = close - stop
        if risk <= 0:
            return None

        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=close + risk * self.reward_risk,
            reason="eci_release",
        )
