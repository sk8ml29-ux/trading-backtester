"""Short-period Donchian breakout — higher trade frequency."""

from __future__ import annotations

import pandas as pd

from backtest.forex_sessions import add_session_columns
from backtest.indicators import add_atr, add_donchian
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexShortBreakoutStrategy(Strategy):
    """Fast Donchian breakout/breakdown with minimal filters."""

    name = "forex_short_breakout"

    def __init__(self, config: BacktestConfig):
        self.rr = getattr(config, "fx_reward_risk", 1.8)
        self.entry_period = config.donchian_entry
        self.exit_period = config.donchian_exit
        self.atr_period = getattr(config, "fx_atr_period", 14)
        self.min_range_atr = getattr(config, "fx_min_range_atr", 0.25)
        self.session = getattr(config, "fx_session_filter", "active")

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_donchian(
            add_atr(add_session_columns(df), self.atr_period),
            self.entry_period,
            self.exit_period,
        )
        return out

    def allows_regime(self, regime: str) -> bool:
        return True

    def _session_ok(self, row: pd.Series) -> bool:
        if self.session == "all":
            return True
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

        atr = float(row.get("atr", 0))
        if atr <= 0 or pd.isna(row.get("donchian_high")):
            return None

        regime = str(row.get("regime", "range"))
        if regime in ("trend_up", "range"):
            sig = self._long(row, atr)
            if sig:
                return sig
        if regime in ("trend_down", "range"):
            return self._short(row, atr)
        return None

    def _long(self, row: pd.Series, atr: float) -> Signal | None:
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
            take_profit=entry + risk * self.rr,
            reason="fx_short_breakout_long",
        )

    def _short(self, row: pd.Series, atr: float) -> Signal | None:
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
            take_profit=entry - risk * self.rr,
            reason="fx_short_breakout_short",
        )
