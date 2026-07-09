"""Bidirectional MACD swing in daily trend during London/NY sessions."""

from __future__ import annotations

import pandas as pd

from backtest.forex_sessions import add_session_columns
from backtest.indicators import add_atr, add_macd, swing_high, swing_low
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexMacdSwingStrategy(Strategy):
    """MACD cross with trend + session filter; ATR/swing stops, higher R:R."""

    name = "forex_macd_swing"

    def __init__(self, config: BacktestConfig):
        self.reward_risk = getattr(config, "fx_reward_risk", 2.5)
        self.swing_lookback = config.swing_lookback
        self.session = getattr(config, "fx_session_filter", "active")
        self.atr_period = getattr(config, "fx_atr_period", 14)
        self.atr_sl = getattr(config, "fx_atr_sl", 1.2)
        self.require_below_zero = getattr(config, "macd_require_below_zero", True)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_macd(add_atr(add_session_columns(df), self.atr_period))
        out["swing_low"] = swing_low(out["low"], lookback=self.swing_lookback)
        out["swing_high"] = swing_high(out["high"], lookback=self.swing_lookback)
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
        if regime == "trend_up":
            return self._long(row, prev)
        if regime == "trend_down":
            return self._short(row, prev)
        return None

    def _long(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = ["macd", "macd_signal", "swing_low", "atr", "close"]
        if any(pd.isna(row.get(c)) for c in required):
            return None

        cross_up = float(prev["macd"]) <= float(prev["macd_signal"]) and float(row["macd"]) > float(row["macd_signal"])
        if not cross_up:
            return None
        if self.require_below_zero and float(row["macd"]) >= 0:
            return None

        entry = float(row["close"])
        stop = min(float(row["swing_low"]), entry - float(row["atr"]) * self.atr_sl)
        risk = entry - stop
        if risk <= 0:
            return None

        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=entry + risk * self.reward_risk,
            reason="fx_macd_swing_long",
        )

    def _short(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = ["macd", "macd_signal", "swing_high", "atr", "close"]
        if any(pd.isna(row.get(c)) for c in required):
            return None

        cross_dn = float(prev["macd"]) >= float(prev["macd_signal"]) and float(row["macd"]) < float(row["macd_signal"])
        if not cross_dn:
            return None
        if self.require_below_zero and float(row["macd"]) <= 0:
            return None

        entry = float(row["close"])
        stop = max(float(row["swing_high"]), entry + float(row["atr"]) * self.atr_sl)
        risk = stop - entry
        if risk <= 0:
            return None

        return Signal(
            side=Side.SHORT,
            stop_loss=stop,
            take_profit=entry - risk * self.reward_risk,
            reason="fx_macd_swing_short",
        )
