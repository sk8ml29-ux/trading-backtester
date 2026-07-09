"""
Triple timeframe confluence — 15m, 30m, 1h cooperate on the SAME symbol.

- 1h sets primary trend bias (EMA21 slope)
- 30m must agree before 15m fires
- Entry: squeeze breakout/breakdown when all relevant TFs align
- Daily regime filters hostile environments
"""

from __future__ import annotations

import pandas as pd

from backtest.indicators import add_atr, add_bb_width_percentile, add_bollinger, swing_high, swing_low
from backtest.mtf_bias import alignment_score, compute_bias_series, merge_higher_bias
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class TripleTfConfluenceStrategy(Strategy):
    name = "triple_tf_confluence"

    def __init__(self, config: BacktestConfig):
        self.cfg = config
        self.entry_tf = config.entry_timeframe or config.timeframe
        self.reward_risk = config.reward_risk
        self.bb_period = config.squeeze_bb_period
        self.width_pct_max = config.squeeze_width_pct_max
        self.width_lookback = config.squeeze_width_lookback
        self.swing_lookback = config.swing_lookback
        self.min_align = getattr(config, "mtf_min_align", None)  # None = all must agree

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        symbol = self.cfg.symbol
        data = add_bollinger(df, period=self.bb_period)
        data = add_bb_width_percentile(data, lookback=self.width_lookback)
        data = add_atr(data, 14)
        data["swing_low"] = swing_low(data["low"], lookback=self.swing_lookback)
        data["swing_high"] = swing_high(data["high"], lookback=self.swing_lookback)
        data["was_squeeze"] = data["bb_width_pct"].shift(1) <= self.width_pct_max
        data["bias_local"] = compute_bias_series(data)

        if self.entry_tf in ("15m", "30m"):
            data = merge_higher_bias(data, symbol, "1h", "bias_1h")
        else:
            data["bias_1h"] = data["bias_local"]

        if self.entry_tf == "15m":
            data = merge_higher_bias(data, symbol, "30m", "bias_30m")
        elif self.entry_tf == "30m":
            data["bias_30m"] = data["bias_local"]
        else:
            data["bias_30m"] = 0

        return data

    def allows_regime(self, regime: str) -> bool:
        return True

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = [
            "bb_upper", "bb_lower", "bb_mid", "was_squeeze", "ema_slow",
            "swing_low", "swing_high", "close", "bias_local",
        ]
        if any(pd.isna(row[c]) for c in required if c in row.index):
            return None

        regime = str(row.get("regime", "range"))
        long_score, short_score, need = alignment_score(row, self.entry_tf)
        min_align = self.min_align if self.min_align is not None else need

        long_ok = long_score >= min_align and regime != "trend_down"
        short_ok = short_score >= min_align and regime == "trend_down"

        if long_ok:
            sig = self._long(row, prev)
            if sig:
                return sig
        if short_ok:
            return self._short(row, prev)
        return None

    def _long(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        close = float(row["close"])
        if close <= float(row["ema_slow"]) or not bool(row["was_squeeze"]):
            return None
        if close <= float(prev["bb_upper"]) or close <= float(row["bb_upper"]):
            return None

        stop = min(float(row["swing_low"]), float(row["bb_mid"]))
        if stop >= close:
            stop = close - float(row.get("atr", close * 0.01)) * 1.5
        risk = close - stop
        if risk <= 0:
            return None

        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=close + risk * self.reward_risk,
            reason=f"mtf_confluence_long_{self.entry_tf}",
        )

    def _short(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        close = float(row["close"])
        if close >= float(row["ema_slow"]) or not bool(row["was_squeeze"]):
            return None
        if close >= float(prev["bb_lower"]) or close >= float(row["bb_lower"]):
            return None

        stop = max(float(row["swing_high"]), float(row["bb_mid"]))
        if stop <= close:
            stop = close + float(row.get("atr", close * 0.01)) * 1.5
        risk = stop - close
        if risk <= 0:
            return None

        return Signal(
            side=Side.SHORT,
            stop_loss=stop,
            take_profit=close - risk * self.reward_risk,
            reason=f"mtf_confluence_short_{self.entry_tf}",
        )
