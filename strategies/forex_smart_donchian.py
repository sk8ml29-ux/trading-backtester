"""Moderate-frequency Donchian with ADX band + session filter — survives forex costs."""

from __future__ import annotations

import pandas as pd
from ta.trend import ADXIndicator

from backtest.forex_sessions import add_session_columns
from backtest.indicators import add_atr, add_donchian
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexSmartDonchianStrategy(Strategy):
    """Donchian 20-28 entry, ADX 15-25 band, active sessions, wider min-range."""

    name = "forex_smart_donchian"

    def __init__(self, config: BacktestConfig):
        self.rr = getattr(config, "fx_reward_risk", config.reward_risk)
        self.entry_period = config.donchian_entry
        self.exit_period = config.donchian_exit
        self.session = getattr(config, "fx_session_filter", "active")
        self.min_range_atr = getattr(config, "fx_min_range_atr", 0.45)
        self.atr_period = getattr(config, "fx_atr_period", 14)
        self.min_adx = getattr(config, "fx_min_adx_trend", 15.0)
        self.max_adx = getattr(config, "fx_max_adx_trend", 28.0)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_donchian(
            add_atr(add_session_columns(df), self.atr_period),
            self.entry_period,
            self.exit_period,
        )
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

    def _adx_ok(self, row: pd.Series) -> bool:
        adx = row.get("adx")
        if pd.isna(adx):
            return False
        v = float(adx)
        return self.min_adx <= v <= self.max_adx

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        if not self._session_ok(row) or not self._adx_ok(row):
            return None

        atr = float(row.get("atr", 0))
        if atr <= 0:
            return None

        regime = str(row.get("regime", "range"))
        if regime == "trend_up":
            return self._long(row, atr)
        if regime == "trend_down":
            return self._short(row, atr)
        return None

    def _long(self, row: pd.Series, atr: float) -> Signal | None:
        required = ["donchian_high", "donchian_entry_low", "close", "ema_slow"]
        if any(pd.isna(row.get(c)) for c in required):
            return None

        entry = float(row["close"])
        if entry <= float(row["donchian_high"]) or entry <= float(row["ema_slow"]):
            return None

        stop = float(row["donchian_entry_low"])
        risk = entry - stop
        if risk <= 0 or risk < atr * self.min_range_atr:
            return None

        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=entry + risk * self.rr,
            reason="smart_donchian_long",
        )

    def _short(self, row: pd.Series, atr: float) -> Signal | None:
        required = ["donchian_entry_low", "donchian_high", "close", "ema_slow"]
        if any(pd.isna(row.get(c)) for c in required):
            return None

        entry = float(row["close"])
        lower = float(row["donchian_entry_low"])
        if entry >= lower or entry >= float(row["ema_slow"]):
            return None

        stop = float(row["donchian_high"])
        risk = stop - entry
        if risk <= 0 or risk < atr * self.min_range_atr:
            return None

        return Signal(
            side=Side.SHORT,
            stop_loss=stop,
            take_profit=entry - risk * self.rr,
            reason="smart_donchian_short",
        )
