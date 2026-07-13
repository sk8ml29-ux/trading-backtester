"""
MACD Bidirectional (MBD) — steg 2 + 3 kombinerat.

Vad det gör:
  - LONG när trenden är uppåt (pris över 200 EMA) + MACD crossar uppåt
  - SHORT när trenden är nedåt (pris under 200 EMA) + MACD crossar nedåt
  - Extra filter: veckovis 50 EMA — tar INGA longs om veckotrenden är bearish
  - Extra filter: veckovis 50 EMA — tar INGA shorts om veckotrenden är bullish

Varför det hjälper BTC/ETH:
  I den OOS-period som testades (okt 2025–jul 2026) var BTC/ETH i sidorörelse
  med inslag av bearish perioder. En long-only bot förlorar i det läget.
  MBD kan tjäna pengar åt BÅDA håll.

Parametrar:
  reward_risk        — hur många gånger risken vi tar i vinst (standard: 2.0)
  swing_lookback     — hur många bars bakåt vi letar swing low/high (standard: 16)
  macd_signal_mode   — cross_below_zero | histogram_flip | either
  weekly_filter      — True = kolla veckovis EMA-trend (rekommenderat)
"""

from __future__ import annotations

import pandas as pd

from backtest.data_loader import fetch_ohlcv
from backtest.indicators import add_macd, swing_high, swing_low
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class MacdBidirectionalStrategy(Strategy):
    """MACD pullback åt båda håll — med vecko-trendfilter."""

    name = "macd_bidirectional"

    def __init__(self, config: BacktestConfig):
        self.reward_risk    = config.reward_risk
        self.swing_lookback = config.swing_lookback
        self.signal_mode    = config.macd_signal_mode
        self.weekly_filter  = True
        self._symbol        = config.symbol
        self._weekly_ema: dict[pd.Timestamp, float] = {}

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        data = add_macd(df)
        data["swing_low"]  = swing_low(data["low"],  lookback=self.swing_lookback)
        data["swing_high"] = swing_high(data["high"], lookback=self.swing_lookback)

        # Hämta veckobars och beräkna 50-veckorns EMA
        if self.weekly_filter:
            self._load_weekly_ema(data.index[0])
            data["weekly_ema50"] = self._align_weekly_ema(data)
        else:
            data["weekly_ema50"] = None

        return data

    def _load_weekly_ema(self, start: pd.Timestamp) -> None:
        try:
            weekly = fetch_ohlcv(
                self._symbol, "1w",
                start="2020-01-01",
                refresh=False,
            )
            if weekly.empty:
                return
            from ta.trend import EMAIndicator
            ema = EMAIndicator(close=weekly["close"], window=50).ema_indicator()
            self._weekly_ema = ema.to_dict()
        except Exception:
            self._weekly_ema = {}

    def _align_weekly_ema(self, df: pd.DataFrame) -> pd.Series:
        if not self._weekly_ema:
            return pd.Series(index=df.index, dtype=float)

        weekly_s = pd.Series(self._weekly_ema).sort_index()
        aligned = (
            weekly_s
            .reindex(df.index.union(weekly_s.index))
            .sort_index()
            .ffill()
            .reindex(df.index)
        )
        return aligned

    def allows_regime(self, regime: str) -> bool:
        # Accepterar alla regimer — strategin hanterar riktning internt
        return True

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = ["macd", "macd_signal", "macd_hist", "ema_slow", "close"]
        if any(pd.isna(row.get(c)) for c in required):
            return None

        close  = float(row["close"])
        ema200 = float(row["ema_slow"])

        # Veckovis trendfilter
        weekly_ema = row.get("weekly_ema50")
        weekly_bullish = True   # standard: inga begränsningar om data saknas
        weekly_bearish = True
        if weekly_ema is not None and not pd.isna(weekly_ema):
            weekly_bullish = close > float(weekly_ema)
            weekly_bearish = close < float(weekly_ema)

        # MACD-signaler
        cross_up   = float(prev["macd"]) <= float(prev["macd_signal"]) and float(row["macd"]) > float(row["macd_signal"])
        cross_down = float(prev["macd"]) >= float(prev["macd_signal"]) and float(row["macd"]) < float(row["macd_signal"])
        hist_up    = float(prev["macd_hist"]) <= 0 and float(row["macd_hist"]) > 0
        hist_down  = float(prev["macd_hist"]) >= 0 and float(row["macd_hist"]) < 0

        if self.signal_mode == "cross_below_zero":
            long_ok  = cross_up
            short_ok = cross_down
        elif self.signal_mode == "histogram_flip":
            long_ok  = hist_up
            short_ok = hist_down
        else:  # either
            long_ok  = cross_up or hist_up
            short_ok = cross_down or hist_down

        # === LONG ===
        if long_ok and close > ema200 and weekly_bullish:
            sl = row.get("swing_low")
            if sl is None or pd.isna(sl):
                return None
            stop = float(sl)
            if stop >= close:
                stop = close * 0.985
            risk = close - stop
            if risk <= 0:
                return None
            return Signal(
                side=Side.LONG,
                stop_loss=stop,
                take_profit=close + risk * self.reward_risk,
                reason="mbd_long",
            )

        # === SHORT ===
        if short_ok and close < ema200 and weekly_bearish:
            sh = row.get("swing_high")
            if sh is None or pd.isna(sh):
                return None
            stop = float(sh)
            if stop <= close:
                stop = close * 1.015
            risk = stop - close
            if risk <= 0:
                return None
            return Signal(
                side=Side.SHORT,
                stop_loss=stop,
                take_profit=close - risk * self.reward_risk,
                reason="mbd_short",
            )

        return None
