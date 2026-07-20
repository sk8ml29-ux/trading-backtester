"""
RSI(2) Mean Reversion (Connors-style)
======================================
En av de mest väldokumenterade edge:arna för aktieindex/ETF:er.
Köper kortsiktigt översålt INOM en långsiktig uppåttrend.

Logik (long-only):
  Filter : close > SMA200  (endast köp i långsiktig uppåttrend)
  Setup  : RSI(2) < oversold_threshold  (kraftigt översålt kortsiktigt)
  Entry  : vid stängning
  TP     : återgång till medelvärdet (SMA-mid) — det är hela poängen
  SL     : ATR-baserad, bred (mean reversion behöver utrymme)

Varför det kompletterar portföljen:
- Trend/breakout-strategier tjänar i trender, sitter stilla i sidled.
- RSI(2) tjänar i sidled/dippar — OKORRELERAD edge.
- När BTC/ETH går sidledes (som nu) fångar den studsarna.

Fungerar bäst på: SPY, QQQ, IWM, index-ETF:er samt likvida aktier.
Fungerar även på crypto i uppåttrend.

Parametrar (via config, med defaults):
  rsi2_period       : RSI-period (default 2)
  rsi2_oversold     : Köpnivå (default 10.0)
  rsi2_trend_sma    : Långsiktigt trendfilter (default 200)
  rsi2_exit_sma     : Medelvärde för TP-mål (default 5)
  rsi2_atr_sl       : ATR-multipel för stop (default 3.0)
  rsi2_max_rr       : Tak för R:R om medel ligger långt bort (default 4.0)
"""
from __future__ import annotations

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator

from backtest.indicators import add_atr
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class Rsi2ReversionStrategy(Strategy):
    """Connors RSI(2) mean reversion — köp översålt i uppåttrend."""

    name = "rsi2_reversion"

    def __init__(self, config: BacktestConfig):
        self.rsi_period   = int(getattr(config, "rsi2_period",    2))
        self.oversold     = float(getattr(config, "rsi2_oversold", 10.0))
        self.trend_sma    = int(getattr(config, "rsi2_trend_sma", 200))
        self.exit_sma     = int(getattr(config, "rsi2_exit_sma",  5))
        self.atr_sl       = float(getattr(config, "rsi2_atr_sl",  3.0))
        self.max_rr       = float(getattr(config, "rsi2_max_rr",  4.0))

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        data = df.copy()
        data["rsi2"] = RSIIndicator(
            close=data["close"], window=self.rsi_period
        ).rsi()
        data["trend_sma"] = SMAIndicator(
            close=data["close"], window=self.trend_sma
        ).sma_indicator()
        data["exit_sma"] = SMAIndicator(
            close=data["close"], window=self.exit_sma
        ).sma_indicator()
        data = add_atr(data, 14)
        return data

    def allows_regime(self, regime: str) -> bool:
        # VIKTIGT: mean reversion MÅSTE tillåtas i alla regimer.
        # En skarp dipp får ofta regime att flagga "trend_down" — men det är
        # exakt då vi vill köpa. Vårt eget SMA200-filter (i generate_signal)
        # sköter den långsiktiga trendkontrollen istället.
        return True

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = ["rsi2", "trend_sma", "exit_sma", "atr", "close"]
        if any(pd.isna(row.get(c)) for c in required):
            return None

        close     = float(row["close"])
        rsi2      = float(row["rsi2"])
        trend_sma = float(row["trend_sma"])
        exit_sma  = float(row["exit_sma"])
        atr       = float(row["atr"])

        if atr <= 0:
            return None

        # Långsiktigt uppåttrend-filter
        if close <= trend_sma:
            return None

        # Kortsiktigt översålt
        if rsi2 >= self.oversold:
            return None

        # Stop: bred ATR-baserad (mean reversion behöver andrum)
        stop = close - atr * self.atr_sl
        risk = close - stop
        if risk <= 0:
            return None

        # TP-mål: återgång till kort medelvärde (exit_sma), men minst 1R
        # och max_rr som tak (undvik orimligt långa mål).
        target = max(exit_sma, close + risk * 1.0)
        rr = (target - close) / risk
        if rr > self.max_rr:
            target = close + risk * self.max_rr
        if target <= close:
            target = close + risk * 1.0

        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=target,
            reason="rsi2_oversold_bounce",
        )
