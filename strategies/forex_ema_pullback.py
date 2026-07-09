"""EMA ribbon pullback in daily trend — low-frequency forex swing."""

from __future__ import annotations

import pandas as pd
from ta.trend import ADXIndicator, EMAIndicator

from backtest.forex_sessions import add_session_columns
from backtest.indicators import add_atr
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexEmaPullbackStrategy(Strategy):
    """Pullback to EMA21 in trend; ADX confirms momentum; session-gated."""

    name = "forex_ema_pullback"

    def __init__(self, config: BacktestConfig):
        self.reward_risk = getattr(config, "fx_reward_risk", 2.2)
        self.ema_period = getattr(config, "fx_ema_period", 21)
        self.atr_period = getattr(config, "fx_atr_period", 14)
        self.atr_sl = getattr(config, "fx_atr_sl", 1.0)
        self.min_adx = getattr(config, "fx_min_adx_trend", 18.0)
        self.session = getattr(config, "fx_session_filter", "active")

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_session_columns(add_atr(df, self.atr_period))
        out["ema_pb"] = EMAIndicator(close=out["close"], window=self.ema_period).ema_indicator()
        out["adx"] = ADXIndicator(
            high=out["high"], low=out["low"], close=out["close"], window=14
        ).adx()
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

        needed = ["ema_pb", "adx", "atr", "close", "regime"]
        if any(pd.isna(row.get(c)) for c in needed):
            return None
        if float(row["adx"]) < self.min_adx:
            return None

        regime = str(row["regime"])
        entry = float(row["close"])
        ema = float(row["ema_pb"])
        prev_close = float(prev["close"]) if not pd.isna(prev.get("close")) else entry
        prev_ema = float(prev["ema_pb"]) if not pd.isna(prev.get("ema_pb")) else ema
        atr = float(row["atr"])

        if regime == "trend_up" and prev_close <= prev_ema and entry > ema:
            stop = entry - atr * self.atr_sl
            risk = entry - stop
            if risk <= 0:
                return None
            return Signal(
                side=Side.LONG,
                stop_loss=stop,
                take_profit=entry + risk * self.reward_risk,
                reason="fx_ema_pullback_long",
            )

        if regime == "trend_down" and prev_close >= prev_ema and entry < ema:
            stop = entry + atr * self.atr_sl
            risk = stop - entry
            if risk <= 0:
                return None
            return Signal(
                side=Side.SHORT,
                stop_loss=stop,
                take_profit=entry - risk * self.reward_risk,
                reason="fx_ema_pullback_short",
            )
        return None
