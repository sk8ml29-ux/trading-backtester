"""
Micro Opening Range Breakout (ORB)
===================================
Princip: Varje ny "session" bygger en range av de första N barsen.
När priset bryter ut ur rangen, ta en position i breakout-riktningen.

Det här är en av de mest välstuderade och empiriskt validerade
short-TF-strategierna. Institutionella spelare sätter ofta sin dagliga
riktning tidigt — ORB fangar det flödet.

Fungerar på:
- 15m (30-min range = 2 bars, 1h range = 4 bars)
- 5m  (30-min range = 6 bars)
- 1m  (30-min range = 30 bars)

Parametrar:
  orb_period     : Antal bars för att bygga range (default 4 = 1h på 15m)
  orb_max_trades : Max trades per session (default 1 — ta bara första breakout)
  orb_rr         : Risk/reward-ratio (default 2.0)
  orb_session_bars: Hur många bars utgör en "session" (default 24 = 6h på 15m)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class MicroOrbStrategy(Strategy):
    """Opening Range Breakout — fångar den initiala riktnings-impulsen."""

    name = "micro_orb"

    def __init__(self, config: BacktestConfig):
        self.orb_period     = getattr(config, "orb_period",      4)
        self.orb_rr         = getattr(config, "orb_rr",          config.reward_risk)
        self.orb_session    = getattr(config, "orb_session_bars", 24)  # 6h på 15m
        self.swing_lookback = config.swing_lookback

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        data = df.copy()
        n    = self.orb_period
        sess = self.orb_session

        # Session-index: vilken session tillhör varje bar?
        data["session_id"] = (np.arange(len(data)) // sess).astype(int)

        # För varje bar: vad är range-high/low för de FÖRSTA n barsen i sessionen?
        orb_highs = []
        orb_lows  = []

        session_groups = data.groupby("session_id")
        session_map: dict[int, tuple[float, float]] = {}

        for sid, grp in session_groups:
            first_n = grp.head(n)
            h = float(first_n["high"].max())
            lo = float(first_n["low"].min())
            session_map[int(sid)] = (h, lo)

        for i, row in data.iterrows():
            sid = int(row["session_id"])
            h, lo = session_map.get(sid, (float("nan"), float("nan")))
            orb_highs.append(h)
            orb_lows.append(lo)

        data["orb_high"] = orb_highs
        data["orb_low"]  = orb_lows
        data["orb_range"] = data["orb_high"] - data["orb_low"]

        # Bar-index inom sessionen (0 = första baren i sessionen)
        data["bar_in_session"] = np.arange(len(data)) % sess

        # ATR (14) för stop-sizing
        high = data["high"]
        low  = data["low"]
        close_prev = data["close"].shift(1)
        tr = pd.concat([
            high - low,
            (high - close_prev).abs(),
            (low  - close_prev).abs(),
        ], axis=1).max(axis=1)
        data["atr"] = tr.rolling(14).mean()

        return data

    def allows_regime(self, regime: str) -> bool:
        # ORB fungerar i alla regimer — breakout sker i trendmiljö
        return True

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        # Vänta tills range är byggd (vi är bortom de första n barsen)
        if int(row.get("bar_in_session", 0)) < self.orb_period:
            return None

        orb_high  = float(row.get("orb_high",  float("nan")))
        orb_low   = float(row.get("orb_low",   float("nan")))
        orb_range = float(row.get("orb_range", float("nan")))
        close     = float(row["close"])
        atr       = float(row.get("atr", float("nan")))

        if any(np.isnan([orb_high, orb_low, orb_range, atr])):
            return None
        if orb_range <= 0 or atr <= 0:
            return None

        # Undvik om range är onormalt stor (gappar, news-events)
        if orb_range > atr * 4:
            return None

        # Long breakout: close stänger OVANFÖR range-high
        if close > orb_high:
            stop = orb_low  # Stop under hela range
            risk = close - stop
            if risk <= 0:
                return None
            return Signal(
                side=Side.LONG,
                stop_loss=stop,
                take_profit=close + risk * self.orb_rr,
                reason="orb_long",
            )

        # Short breakout: close stänger UNDER range-low
        if close < orb_low:
            stop = orb_high  # Stop ovanför hela range
            risk = stop - close
            if risk <= 0:
                return None
            return Signal(
                side=Side.SHORT,
                stop_loss=stop,
                take_profit=close - risk * self.orb_rr,
                reason="orb_short",
            )

        return None
