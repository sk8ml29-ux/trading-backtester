"""Donchian breakout/breakdown in trend regime during active forex sessions."""

from __future__ import annotations

import pandas as pd

from backtest.forex_sessions import add_session_columns
from backtest.indicators import add_atr, add_donchian
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexDonchianTrendStrategy(Strategy):
    """Trend-only Donchian with session filter and ATR minimum range."""

    name = "forex_donchian_trend"

    def __init__(self, config: BacktestConfig):
        self.reward_risk = getattr(config, "fx_reward_risk", config.reward_risk)
        self.entry_period = config.donchian_entry
        self.exit_period = config.donchian_exit
        self.session = getattr(config, "fx_session_filter", "active")
        self.min_range_atr = getattr(config, "fx_min_range_atr", 0.5)
        self.atr_period = getattr(config, "fx_atr_period", 14)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_donchian(add_atr(add_session_columns(df), self.atr_period), self.entry_period, self.exit_period)
        return out

    def allows_regime(self, regime: str) -> bool:
        return regime in ("trend_up", "trend_down")

    def _session_ok(self, row: pd.Series) -> bool:
        if self.session == "london":
            return bool(row.get("london_open"))
        if self.session == "ny":
            return bool(row.get("ny_overlap"))
        if self.session == "active":
            return bool(row.get("london_open") or row.get("ny_overlap"))
        return True

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        if not self._session_ok(row):
            return None

        regime = str(row.get("regime", "range"))
        atr = float(row.get("atr", 0))
        if atr <= 0:
            return None

        if regime == "trend_up":
            return self._long(row, atr)
        if regime == "trend_down":
            return self._short(row, atr)
        return None

    def _long(self, row: pd.Series, atr: float) -> Signal | None:
        required = ["donchian_high", "donchian_entry_low", "close", "high"]
        if any(pd.isna(row.get(c)) for c in required):
            return None

        entry = float(row["close"])
        upper = float(row["donchian_high"])
        if entry <= upper:
            return None

        stop = float(row["donchian_entry_low"])
        risk = entry - stop
        if risk <= 0 or risk < atr * self.min_range_atr:
            return None

        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=entry + risk * self.reward_risk,
            reason="fx_donchian_trend_long",
        )

    def _short(self, row: pd.Series, atr: float) -> Signal | None:
        required = ["donchian_entry_low", "donchian_high", "close", "low"]
        if any(pd.isna(row.get(c)) for c in required):
            return None

        entry = float(row["close"])
        lower = float(row["donchian_entry_low"])
        if entry >= lower:
            return None

        stop = float(row["donchian_high"])
        risk = stop - entry
        if risk <= 0 or risk < atr * self.min_range_atr:
            return None

        return Signal(
            side=Side.SHORT,
            stop_loss=stop,
            take_profit=entry - risk * self.reward_risk,
            reason="fx_donchian_trend_short",
        )
