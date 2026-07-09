"""
Velocity Rejection Scalp (VRS) — proprietary 15m scalping.

Two complementary entry modes (unusual combo):
  A) Liquidity sweep fade — stop-run beyond micro extreme + wick rejection
  B) Impulse snap — post-compression burst, enter on first shallow pullback

Tight stops (~0.5–0.8 ATR), quick 1.2–1.35R targets. 1h bias filter optional.
"""

from __future__ import annotations

import pandas as pd
from ta.trend import EMAIndicator

from backtest.indicators import add_atr, add_bb_width_percentile, add_bollinger
from backtest.mtf_bias import merge_higher_bias
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class VelocityRejectionScalpStrategy(Strategy):
    name = "velocity_rejection"

    def __init__(self, config: BacktestConfig):
        self.cfg = config
        self.swing_lb = getattr(config, "vrs_swing_lookback", 8)
        self.wick_min = getattr(config, "vrs_wick_ratio", 0.48)
        self.body_max = getattr(config, "vrs_body_max_ratio", 0.42)
        self.sweep_atr = getattr(config, "vrs_sweep_atr", 0.07)
        self.stop_pad = getattr(config, "vrs_stop_pad_atr", 0.05)
        self.reward_risk = getattr(config, "vrs_reward_risk", 1.28)
        self.vol_mult = getattr(config, "vrs_volume_mult", 1.05)
        self.use_1h_filter = getattr(config, "vrs_use_1h_filter", True)
        self.snap_pullback = getattr(config, "vrs_snap_pullback", 0.42)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        data = add_bollinger(df, period=20)
        data = add_bb_width_percentile(data, lookback=80)
        data = add_atr(data, 14)
        data["ema_snap"] = EMAIndicator(close=data["close"], window=8).ema_indicator()
        data["ema_trend"] = EMAIndicator(close=data["close"], window=21).ema_indicator()

        data["bar_range"] = data["high"] - data["low"]
        body_top = data[["open", "close"]].max(axis=1)
        body_bot = data[["open", "close"]].min(axis=1)
        data["body"] = (body_top - body_bot).abs()
        data["lower_wick"] = body_bot - data["low"]
        data["upper_wick"] = data["high"] - body_top

        data["micro_low"] = data["low"].shift(1).rolling(self.swing_lb).min()
        data["micro_high"] = data["high"].shift(1).rolling(self.swing_lb).max()
        data["range_high"] = data["high"].shift(1).rolling(5).max()
        data["range_low"] = data["low"].shift(1).rolling(5).min()

        if "volume" in data.columns:
            data["vol_ma"] = data["volume"].rolling(15).mean()
        else:
            data["vol_ma"] = pd.NA

        data["compressed"] = data["bb_width_pct"] <= 0.30
        data["recent_compressed"] = data["compressed"].rolling(6).max().fillna(0).astype(bool)

        # Impulse leg markers
        data["impulse_up"] = (
            (data["close"] > data["open"])
            & ((data["close"] - data["open"]) > data["atr"] * 0.55)
            & (data["close"] > data["range_high"])
        )
        data["impulse_dn"] = (
            (data["close"] < data["open"])
            & ((data["open"] - data["close"]) > data["atr"] * 0.55)
            & (data["close"] < data["range_low"])
        )
        data["last_impulse_high"] = data["high"].where(data["impulse_up"]).ffill()
        data["last_impulse_low"] = data["low"].where(data["impulse_dn"]).ffill()

        if self.use_1h_filter:
            data = merge_higher_bias(data, self.cfg.symbol, "1h", "bias_1h")
        else:
            data["bias_1h"] = 0

        return data

    def allows_regime(self, regime: str) -> bool:
        return True

    def _vol_ok(self, row: pd.Series) -> bool:
        if self.vol_mult <= 0 or pd.isna(row.get("vol_ma")) or float(row["vol_ma"]) <= 0:
            return True
        return float(row.get("volume", 0)) >= float(row["vol_ma"]) * self.vol_mult

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        if pd.isna(row.get("atr")) or float(row["atr"]) <= 0:
            return None

        bias = int(row.get("bias_1h", 0))
        sig = self._sweep_long(row, bias) or self._sweep_short(row, bias)
        if sig:
            return sig
        return self._snap_long(row, prev, bias) or self._snap_short(row, prev, bias)

    def _sweep_long(self, row: pd.Series, bias: int) -> Signal | None:
        if bias < 0:
            return None
        atr = float(row["atr"])
        bar_range = float(row["bar_range"])
        if bar_range <= 0 or not self._vol_ok(row):
            return None

        micro_low = float(row["micro_low"])
        close = float(row["close"])
        low = float(row["low"])
        body_ratio = float(row["body"]) / bar_range
        wick_ratio = float(row["lower_wick"]) / bar_range

        if not (
            low < micro_low - atr * self.sweep_atr
            and close > micro_low
            and wick_ratio >= self.wick_min
            and body_ratio <= self.body_max
        ):
            return None

        stop = low - atr * self.stop_pad
        risk = close - stop
        if risk <= 0:
            return None
        return Signal(Side.LONG, stop, close + risk * self.reward_risk, "vrs_sweep_long")

    def _sweep_short(self, row: pd.Series, bias: int) -> Signal | None:
        if bias > 0:
            return None
        atr = float(row["atr"])
        bar_range = float(row["bar_range"])
        if bar_range <= 0 or not self._vol_ok(row):
            return None

        micro_high = float(row["micro_high"])
        close = float(row["close"])
        high = float(row["high"])
        body_ratio = float(row["body"]) / bar_range
        wick_ratio = float(row["upper_wick"]) / bar_range

        if not (
            high > micro_high + atr * self.sweep_atr
            and close < micro_high
            and wick_ratio >= self.wick_min
            and body_ratio <= self.body_max
        ):
            return None

        stop = high + atr * self.stop_pad
        risk = stop - close
        if risk <= 0:
            return None
        return Signal(Side.SHORT, stop, close - risk * self.reward_risk, "vrs_sweep_short")

    def _snap_long(self, row: pd.Series, prev: pd.Series, bias: int) -> Signal | None:
        if bias < 0 or not bool(row.get("recent_compressed", False)):
            return None
        if pd.isna(row.get("last_impulse_high")) or pd.isna(prev.get("last_impulse_high")):
            return None
        if float(row["last_impulse_high"]) != float(prev["last_impulse_high"]):
            return None  # only on bar after impulse

        atr = float(row["atr"])
        close = float(row["close"])
        imp_low = float(prev["low"])
        imp_high = float(prev["high"])
        pb = imp_high - (imp_high - imp_low) * self.snap_pullback

        if not (close <= pb and close > float(row["ema_snap"]) and close > float(row["open"])):
            return None
        if float(row["ema_snap"]) < float(row["ema_trend"]):
            return None

        stop = min(float(row["low"]), imp_low) - atr * self.stop_pad
        risk = close - stop
        if risk <= 0 or risk > atr * 0.9:
            return None
        return Signal(Side.LONG, stop, close + risk * self.reward_risk, "vrs_snap_long")

    def _snap_short(self, row: pd.Series, prev: pd.Series, bias: int) -> Signal | None:
        if bias > 0 or not bool(row.get("recent_compressed", False)):
            return None
        if pd.isna(row.get("last_impulse_low")) or pd.isna(prev.get("last_impulse_low")):
            return None
        if float(row["last_impulse_low"]) != float(prev["last_impulse_low"]):
            return None

        atr = float(row["atr"])
        close = float(row["close"])
        imp_low = float(prev["low"])
        imp_high = float(prev["high"])
        pb = imp_low + (imp_high - imp_low) * self.snap_pullback

        if not (close >= pb and close < float(row["ema_snap"]) and close < float(row["open"])):
            return None
        if float(row["ema_snap"]) > float(row["ema_trend"]):
            return None

        stop = max(float(row["high"]), imp_high) + atr * self.stop_pad
        risk = stop - close
        if risk <= 0 or risk > atr * 0.9:
            return None
        return Signal(Side.SHORT, stop, close - risk * self.reward_risk, "vrs_snap_short")
