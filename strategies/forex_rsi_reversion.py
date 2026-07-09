"""Forex RSI mean reversion — bidirectional, looser thresholds, session-aware."""

from __future__ import annotations

import pandas as pd
from ta.trend import ADXIndicator

from backtest.forex_sessions import add_session_columns
from backtest.indicators import add_atr, add_rsi
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexRsiReversionStrategy(Strategy):
    """Fade RSI extremes in low-ADX / range conditions during active sessions."""

    name = "forex_rsi_reversion"

    def __init__(self, config: BacktestConfig):
        self.rsi_period = getattr(config, "fx_rsi_period", 14)
        self.oversold = getattr(config, "fx_rsi_oversold", 35.0)
        self.overbought = getattr(config, "fx_rsi_overbought", 65.0)
        self.rr = getattr(config, "fx_reward_risk", 1.5)
        self.atr_period = getattr(config, "fx_atr_period", 14)
        self.atr_sl = getattr(config, "fx_atr_sl", 1.0)
        self.max_adx = getattr(config, "fx_max_adx_range", 25.0)
        self.session = getattr(config, "fx_session_filter", "active")

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_rsi(add_atr(add_session_columns(df), self.atr_period), self.rsi_period)
        out["adx"] = ADXIndicator(
            high=out["high"], low=out["low"], close=out["close"], window=14
        ).adx()
        return out

    def allows_regime(self, regime: str) -> bool:
        return regime in ("range", "trend_up", "trend_down")

    def _session_ok(self, row: pd.Series) -> bool:
        if self.session == "london":
            return bool(row.get("london_open"))
        if self.session == "ny":
            return bool(row.get("ny_overlap"))
        if self.session == "asian":
            return bool(row.get("asian_session"))
        if self.session == "active":
            return bool(row.get("london_open") or row.get("ny_overlap"))
        return True

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        if not self._session_ok(row):
            return None

        needed = ["rsi", "atr", "close", "adx"]
        if any(pd.isna(row.get(c)) for c in needed):
            return None
        if any(pd.isna(prev.get(c)) for c in ["rsi"]):
            return None

        if float(row["adx"]) > self.max_adx:
            return None

        entry = float(row["close"])
        atr = float(row["atr"])
        if atr <= 0:
            return None

        cross_up = float(prev["rsi"]) < self.oversold and float(row["rsi"]) >= self.oversold
        if cross_up:
            stop = entry - atr * self.atr_sl
            risk = entry - stop
            if risk <= 0:
                return None
            return Signal(
                side=Side.LONG,
                stop_loss=stop,
                take_profit=entry + risk * self.rr,
                reason="fx_rsi_oversold",
            )

        cross_down = float(prev["rsi"]) > self.overbought and float(row["rsi"]) <= self.overbought
        if cross_down:
            stop = entry + atr * self.atr_sl
            risk = stop - entry
            if risk <= 0:
                return None
            return Signal(
                side=Side.SHORT,
                stop_loss=stop,
                take_profit=entry - risk * self.rr,
                reason="fx_rsi_overbought",
            )
        return None
