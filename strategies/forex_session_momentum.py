"""London/NY session momentum — looser EMA + price action entries."""

from __future__ import annotations

import pandas as pd
from ta.trend import EMAIndicator

from backtest.forex_sessions import add_session_columns
from backtest.indicators import add_atr
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexSessionMomentumStrategy(Strategy):
    """Momentum continuation during London or NY overlap with relaxed filters."""

    name = "forex_session_momentum"

    def __init__(self, config: BacktestConfig):
        self.rr = getattr(config, "fx_reward_risk", 1.8)
        self.ema_fast = getattr(config, "fx_ema_fast", 9)
        self.ema_slow = getattr(config, "fx_ema_period", 21)
        self.atr_period = getattr(config, "fx_atr_period", 14)
        self.atr_sl = getattr(config, "fx_atr_sl", 0.9)
        self.session = getattr(config, "fx_session_filter", "active")
        self.require_trend = getattr(config, "fx_require_trend", False)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_session_columns(add_atr(df, self.atr_period))
        out["ema_fast"] = EMAIndicator(close=out["close"], window=self.ema_fast).ema_indicator()
        out["ema_slow"] = EMAIndicator(close=out["close"], window=self.ema_slow).ema_indicator()
        return out

    def allows_regime(self, regime: str) -> bool:
        if self.require_trend:
            return regime in ("trend_up", "trend_down")
        return True

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

        needed = ["ema_fast", "ema_slow", "atr", "close", "high", "low"]
        if any(pd.isna(row.get(c)) for c in needed):
            return None

        entry = float(row["close"])
        atr = float(row["atr"])
        if atr <= 0:
            return None

        ema_f = float(row["ema_fast"])
        ema_s = float(row["ema_slow"])
        prev_close = float(prev["close"]) if not pd.isna(prev.get("close")) else entry
        regime = str(row.get("regime", "range"))

        bullish = ema_f > ema_s and entry > ema_f and float(row["close"]) > float(row["open"])
        bearish = ema_f < ema_s and entry < ema_f and float(row["close"]) < float(row["open"])

        if regime in ("trend_up", "range") and bullish and prev_close <= ema_f:
            stop = entry - atr * self.atr_sl
            risk = entry - stop
            if risk <= 0:
                return None
            return Signal(
                side=Side.LONG,
                stop_loss=stop,
                take_profit=entry + risk * self.rr,
                reason="session_momentum_long",
            )

        if regime in ("trend_down", "range") and bearish and prev_close >= ema_f:
            stop = entry + atr * self.atr_sl
            risk = stop - entry
            if risk <= 0:
                return None
            return Signal(
                side=Side.SHORT,
                stop_loss=stop,
                take_profit=entry - risk * self.rr,
                reason="session_momentum_short",
            )
        return None
