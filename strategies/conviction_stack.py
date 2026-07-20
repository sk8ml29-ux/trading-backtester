"""
Conviction Stack — checklist-baserad positionering ("kryddan")
==============================================================
Hittar en trend-pullback och kör den genom en 10-punkters confluence-checklista.
Ju fler villkor som uppfylls (score), desto större satsning:

  score < min_checks       → ingen trade
  min_checks <= score < high_checks → normal risk (1x)
  score >= high_checks     → HÖG CONVICTION → stor risk (conviction_risk_mult, t.ex. 3x)

Detta är den mer aggressiva "krydda"-strategin. Den tar få men mycket
selektiva trades, och satsar stort bara när nästan allt pekar åt samma håll.

Checklista (long, trend-continuation):
  1. close > SMA200            — långsiktig uppåttrend
  2. EMA9 > EMA21 > EMA50      — ribbon i ordning
  3. ADX > adx_min            — genuin trend (ej chop)
  4. RSI återhämtar från dip  — RSI korsar upp över rsi_recover
  5. MACD-histogram vänder upp
  6. Volym > snitt            — flöde bekräftar
  7. regime == trend_up       — högre TF bekräftar
  8. pullback klar            — close tillbaka över EMA21
  9. bullish candle           — close > open
 10. ej överutsträckt         — close inom overext_atr * ATR från EMA21

Parametrar (config, med defaults):
  conviction_min_checks    (6)
  conviction_high_checks   (8)
  conviction_risk_mult     (3.0)
  conviction_adx_min       (20.0)
  conviction_rsi_recover   (45.0)
  conviction_overext_atr   (2.0)
"""
from __future__ import annotations

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, SMAIndicator

from backtest.indicators import add_atr, add_ema_stack, add_macd, swing_low
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ConvictionStackStrategy(Strategy):
    """Checklist-scored trend pullback med conviction-baserad risk."""

    name = "conviction_stack"

    def __init__(self, config: BacktestConfig):
        self.reward_risk    = config.reward_risk
        self.swing_lookback = config.swing_lookback
        self.min_checks   = int(getattr(config, "conviction_min_checks", 6))
        self.high_checks  = int(getattr(config, "conviction_high_checks", 8))
        self.risk_mult    = float(getattr(config, "conviction_risk_mult", 3.0))
        self.adx_min      = float(getattr(config, "conviction_adx_min", 20.0))
        self.rsi_recover  = float(getattr(config, "conviction_rsi_recover", 45.0))
        self.overext_atr  = float(getattr(config, "conviction_overext_atr", 2.0))

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        data = add_ema_stack(df, periods=(9, 21, 50))
        data = add_macd(data)
        data = add_atr(data, 14)
        data["sma200"] = SMAIndicator(close=data["close"], window=200).sma_indicator()
        data["rsi"] = RSIIndicator(close=data["close"], window=14).rsi()
        adx = ADXIndicator(high=data["high"], low=data["low"], close=data["close"], window=14)
        data["adx"] = adx.adx()
        data["vol_avg"] = data["volume"].rolling(20).mean() if "volume" in data.columns else 0.0
        data["swing_low"] = swing_low(data["low"], lookback=self.swing_lookback)
        return data

    def allows_regime(self, regime: str) -> bool:
        return regime != "trend_down"

    def _score(self, row: pd.Series, prev: pd.Series) -> int:
        checks = 0
        close = float(row["close"])
        # 1. Långsiktig uppåttrend
        if not pd.isna(row.get("sma200")) and close > float(row["sma200"]):
            checks += 1
        # 2. EMA-ribbon i ordning
        if (not pd.isna(row.get("ema_9")) and not pd.isna(row.get("ema_50"))
                and float(row["ema_9"]) > float(row["ema_21"]) > float(row["ema_50"])):
            checks += 1
        # 3. ADX trend
        if not pd.isna(row.get("adx")) and float(row["adx"]) > self.adx_min:
            checks += 1
        # 4. RSI återhämtar
        if (not pd.isna(row.get("rsi")) and not pd.isna(prev.get("rsi"))
                and float(prev["rsi"]) < self.rsi_recover <= float(row["rsi"])):
            checks += 1
        # 5. MACD-hist vänder upp
        if (not pd.isna(row.get("macd_hist")) and not pd.isna(prev.get("macd_hist"))
                and float(row["macd_hist"]) > float(prev["macd_hist"])):
            checks += 1
        # 6. Volym över snitt
        va = row.get("vol_avg", 0)
        if "volume" in row and not pd.isna(va) and float(va) > 0 and float(row["volume"]) > float(va):
            checks += 1
        # 7. Högre TF-regim
        if str(row.get("regime", "range")) == "trend_up":
            checks += 1
        # 8. Pullback klar (close över EMA21)
        if not pd.isna(row.get("ema_21")) and close > float(row["ema_21"]):
            checks += 1
        # 9. Bullish candle
        if close > float(row["open"]):
            checks += 1
        # 10. Ej överutsträckt
        if (not pd.isna(row.get("ema_21")) and not pd.isna(row.get("atr"))
                and float(row["atr"]) > 0
                and abs(close - float(row["ema_21"])) < self.overext_atr * float(row["atr"])):
            checks += 1
        return checks

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = ["close", "ema_21", "atr", "swing_low", "sma200"]
        if any(pd.isna(row.get(c)) for c in required):
            return None

        close = float(row["close"])
        # Grundkrav: uppåttrend
        if close <= float(row["sma200"]):
            return None

        score = self._score(row, prev)
        if score < self.min_checks:
            return None

        # Stop: swing low, med ATR-golv
        atr = float(row["atr"])
        stop = min(float(row["swing_low"]), close - atr * 1.0)
        risk = close - stop
        if risk <= 0:
            return None

        # Conviction-sizing
        risk_mult = self.risk_mult if score >= self.high_checks else 1.0
        conviction = "HÖG" if score >= self.high_checks else "normal"

        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=close + risk * self.reward_risk,
            reason=f"conviction_{conviction}_{score}checks",
            risk_mult=risk_mult,
        )
