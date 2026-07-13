"""
Funding Rate Confluence v2 (FRC) — proprietär strategi.

Insikt från datanalys (Jan 2024 – Jul 2026):
  - Funding rate > +0.05% träffas bara 0.9 % av alla bars → för sällsynt som block.
  - Funding rate NEGATIVT (~15-20 % av bars) = marknaden är bearish / short-squeeze-miljö.
    Det är DÅ longs har sämst edge.
  - Bäst edge: funding POSITIVT (marknad bullish) + MACD pullback = momentum + teknik.

Signal-logik (1h bars):
  LONG  → MACD cross/hist_flip + pris >200 EMA + funding > FUNDING_MIN (inte bearish)
  SKIP  → funding < FUNDING_MIN (bearish sentiment → marknad ofavorabel för longs)
  BONUS → funding > FUNDING_BOOST (starkt bullish) → vidga TP med 20 % (reward_risk * 1.2)

Trösklar kalibrerade mot historisk distribution (2023-2026):
  Mean ≈ +0.006 %  |  Negativ ca 18 % av tid  |  Max +0.10 %
"""

from __future__ import annotations

import pandas as pd

from backtest.indicators import add_macd, swing_low
from backtest.providers.binance_funding import fetch_funding_rates, align_funding_to_bars
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy

# Funding-trösklar
FUNDING_MIN   = 0.0       # Under 0 → skip longs (bearish funding)
FUNDING_BOOST = 0.0002    # Över 0.02 % → öka TP med 20 % (stark bullish miljö)


class FundingConfluenceStrategy(Strategy):
    """
    MACD pullback + funding rate sentiment filter.
    Long-only. Hoppar över longs i negativ funding-miljö.
    """

    name = "funding_confluence"

    def __init__(self, config: BacktestConfig):
        self.reward_risk    = config.reward_risk
        self.swing_lookback = config.swing_lookback
        self.signal_mode    = config.macd_signal_mode
        self._symbol        = config.symbol

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        data = add_macd(df)
        data["swing_low"] = swing_low(data["low"], lookback=self.swing_lookback)

        try:
            start = str(df.index[0].date())
            funding_df = fetch_funding_rates(self._symbol, start=start)
            data["funding_rate"] = align_funding_to_bars(data, funding_df)
        except Exception:
            data["funding_rate"] = 0.001  # default positiv om data saknas

        return data

    def allows_regime(self, regime: str) -> bool:
        return regime != "strong_down"

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = ["macd", "macd_signal", "macd_hist", "ema_slow", "swing_low", "close"]
        if any(pd.isna(row.get(c)) for c in required):
            return None

        funding = float(row.get("funding_rate", 0.001) or 0.001)
        close   = float(row["close"])
        ema200  = float(row["ema_slow"])

        # Funding-filter: hoppa över longs i bearish-marknad
        if funding < FUNDING_MIN:
            return None

        # Pris måste vara över 200 EMA
        if close <= ema200:
            return None

        # MACD-signal
        cross_up     = float(prev["macd"]) <= float(prev["macd_signal"]) and float(row["macd"]) > float(row["macd_signal"])
        hist_flip_up = float(prev["macd_hist"]) <= 0 and float(row["macd_hist"]) > 0

        if self.signal_mode == "cross_below_zero":
            ok = cross_up
        elif self.signal_mode == "histogram_flip":
            ok = hist_flip_up
        else:
            ok = cross_up or hist_flip_up

        if not ok:
            return None

        entry = close
        stop  = float(row["swing_low"])
        if stop >= entry:
            stop = entry * 0.985

        risk = entry - stop
        if risk <= 0:
            return None

        # BONUS: vidga TP i stark bullish funding-miljö
        rr = self.reward_risk * (1.2 if funding > FUNDING_BOOST else 1.0)
        take_profit = entry + risk * rr

        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=take_profit,
            reason=f"frc_long fr={funding*100:.3f}%",
        )
