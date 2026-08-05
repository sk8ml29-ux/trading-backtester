"""PredictiveValueAccumulation (Köp & Behåll).

Honest framing up front: NOTHING in markets is truly "garanterad". What this
module actually computes is an *empirical conditional probability*: "the
last N times this instrument's price was this extreme relative to its own
trailing distribution, what fraction of the time -- and by how much -- did
it recover over the next `horizon_days`?" That is a real, backtestable,
falsifiable statistic (rolling-percentile historical-analog method). It is
gated hard (min sample size + min win rate) so a thin, unreliable signal is
rejected rather than dressed up as a "guarantee". Validate every target with
walk-forward/OOS (see research/paper_forward.py) before trusting it live.

Two building blocks:
  - the core statistic works on ANY instrument you can pull OHLCV bars for
    (commodities, equities, crypto, FX) via the repo's existing
    backtest.data_loader.fetch_ohlcv.
  - an optional pluggable `ContextSignal` supplies a *confirming* macro
    feature (e.g. Swedish day-ahead electricity being in an extreme
    oversupply regime, which historically drags down correlated energy
    commodities/utilities) that can raise or lower confidence. Electricity
    itself isn't "held" directly (retail can't warehouse spot power) -- it's
    used as a supply/demand signal for a tradable, correlated instrument.
"""

from __future__ import annotations

import abc
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import aiohttp
import pandas as pd

from global_hunter.contracts import ActionType, MarketType, Opportunity
from global_hunter.scanner.base import UniversalValueModule
from global_hunter.scanner.http_venue import DEFAULT_TIMEOUT_S, USER_AGENT

logger = logging.getLogger("global_hunter.scanner.value_accumulation")


class ContextSignal(abc.ABC):
    """A macro supply/demand feature that can confirm (or veto) a target's thesis."""

    name: str = "unnamed_context_signal"

    @abc.abstractmethod
    async def zscore(self) -> float:
        """Positive = currently expensive/high-demand regime. Negative = cheap/oversupplied."""

    async def aclose(self) -> None:
        pass


class SwedishElectricityContextSignal(ContextSignal):
    """Day-ahead spot price z-score for a Swedish bidding zone (SE1-SE4),
    sourced from the free public API https://www.elprisetjustnu.se (no key,
    no auth -- backed by Nord Pool day-ahead auction data).
    """

    def __init__(self, zone: str = "SE3", lookback_days: int = 90) -> None:
        if zone not in {"SE1", "SE2", "SE3", "SE4"}:
            raise ValueError("zone must be one of SE1, SE2, SE3, SE4")
        self.zone = zone
        self.name = f"se_electricity_{zone}"
        self.lookback_days = lookback_days
        self._session: aiohttp.ClientSession | None = None

    async def _session_ref(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_S),
                headers={"User-Agent": USER_AGENT},
            )
        return self._session

    async def _fetch_day(self, day: date) -> float | None:
        url = f"https://www.elprisetjustnu.se/api/v1/prices/{day.year}/{day.month:02d}-{day.day:02d}_{self.zone}.json"
        session = await self._session_ref()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                rows = await resp.json()
        except Exception:  # noqa: BLE001 - one missing day must not break the average
            return None
        if not rows:
            return None
        return float(sum(r["SEK_per_kWh"] for r in rows) / len(rows))

    async def zscore(self) -> float:
        today = datetime.now(timezone.utc).date()
        days = [today - timedelta(days=i) for i in range(self.lookback_days)]
        daily_avgs = await asyncio.gather(*(self._fetch_day(d) for d in days))
        series = pd.Series([v for v in daily_avgs if v is not None])
        if len(series) < 14:
            raise RuntimeError(f"{self.name}: not enough recent daily averages ({len(series)})")
        current, history = series.iloc[0], series.iloc[1:]
        std = history.std()
        if not std or pd.isna(std):
            return 0.0
        return float((current - history.mean()) / std)

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


@dataclass(frozen=True)
class AccumulationTarget:
    instrument: str
    symbol: str                  # ticker resolvable by backtest.data_loader.fetch_ohlcv
    market_type: MarketType
    timeframe: str = "1d"
    lookback_start: str = "2012-01-01"
    percentile_floor: float = 0.10     # must be in the cheapest 10% of its own trailing window
    min_hist_win_rate: float = 0.70    # historical analogs must have recovered 70%+ of the time
    min_analog_count: int = 25         # need enough history to trust the stat
    rolling_window_days: int = 252
    context_signal: ContextSignal | None = None


def _historical_analog_stats(
    closes: "pd.Series", horizon_days: int, rolling_window_days: int, percentile_floor: float
) -> tuple[float | None, float, float, int]:
    """Vectorized rolling-percentile-rank + forward-return analog scan.

    Returns (current_percentile_rank, historical_win_rate, avg_forward_return, analog_count).
    """
    min_len = rolling_window_days + horizon_days + 30
    if len(closes) < min_len:
        return None, 0.0, 0.0, 0

    rolling_rank = closes.rolling(rolling_window_days).rank(pct=True)
    current_rank = rolling_rank.iloc[-1]
    if pd.isna(current_rank):
        return None, 0.0, 0.0, 0

    forward_return = closes.shift(-horizon_days) / closes - 1.0
    analog_mask = rolling_rank <= percentile_floor
    analogs = forward_return[analog_mask].dropna()
    if len(analogs) < 5:
        return float(current_rank), 0.0, 0.0, int(len(analogs))

    win_rate = float((analogs > 0).mean())
    avg_return = float(analogs.mean())
    return float(current_rank), win_rate, avg_return, int(len(analogs))


class PredictiveValueAccumulation(UniversalValueModule):
    """Buy-and-hold module: flags targets that are historically-extreme cheap
    with a statistically well-sampled track record of mean-reverting upward.
    """

    name = "predictive_value_accumulation"
    market_types = (MarketType.COMMODITY, MarketType.EQUITY, MarketType.ENERGY, MarketType.CRYPTO)
    poll_interval_s = 3600.0  # daily-bar signal; hourly re-check is plenty

    def __init__(self, targets: list[AccumulationTarget] | None = None, forward_horizon_days: int = 30) -> None:
        self.targets = targets if targets is not None else default_targets()
        self.forward_horizon_days = forward_horizon_days

    async def scan(self) -> list[Opportunity]:
        results = await asyncio.gather(
            *(self._evaluate(target) for target in self.targets), return_exceptions=True
        )
        opportunities: list[Opportunity] = []
        for target, result in zip(self.targets, results):
            if isinstance(result, BaseException):
                logger.debug("%s: target %s failed: %s", self.name, target.instrument, result)
                continue
            if result is not None:
                opportunities.append(result)
        return opportunities

    async def _evaluate(self, target: AccumulationTarget) -> Opportunity | None:
        from backtest.data_loader import fetch_ohlcv  # local import: keep global_hunter decoupled from backtest/ at import time

        df = await asyncio.to_thread(
            fetch_ohlcv, target.symbol, target.timeframe, target.lookback_start, None, None, True, False, None
        )
        if df is None or len(df) < 60:
            return None

        current_rank, win_rate, avg_return, analog_count = _historical_analog_stats(
            df["close"], self.forward_horizon_days, target.rolling_window_days, target.percentile_floor
        )
        if current_rank is None or current_rank > target.percentile_floor:
            return None
        if win_rate < target.min_hist_win_rate or analog_count < target.min_analog_count:
            return None

        confidence = min(0.90, win_rate)
        context_note: dict = {}
        if target.context_signal is not None:
            try:
                z = await target.context_signal.zscore()
                context_note = {"context_signal": target.context_signal.name, "context_zscore": z}
                if z < -0.5:
                    confidence = min(0.93, confidence + 0.05)  # supply glut confirms the underpricing thesis
                elif z > 1.0:
                    confidence = max(0.0, confidence - 0.10)  # demand spike contradicts it -- de-rate
            except Exception as exc:  # noqa: BLE001 - a broken context signal must not block the core stat
                logger.debug("%s: context signal failed for %s: %s", self.name, target.instrument, exc)

        if confidence < target.min_hist_win_rate:
            return None

        return Opportunity(
            id=f"accum:{target.instrument}:{int(time.time() * 1000)}",
            source=self.name,
            instrument=target.instrument,
            market_type=target.market_type,
            edge_pct=avg_return * 100.0,
            confidence=confidence,
            action=ActionType.BUY_AND_HOLD,
            horizon_days=float(self.forward_horizon_days),
            detected_at=datetime.now(timezone.utc),
            raw={
                "percentile_rank": current_rank,
                "historical_win_rate": win_rate,
                "analog_count": analog_count,
                **context_note,
            },
        )

    async def aclose(self) -> None:
        signals = {id(t.context_signal): t.context_signal for t in self.targets if t.context_signal is not None}
        await asyncio.gather(*(s.aclose() for s in signals.values()), return_exceptions=True)


def default_targets() -> list[AccumulationTarget]:
    """Entrepreneur's starting universe: liquid global commodities, one of
    which (natural gas) is paired with the Swedish electricity supply/demand
    signal since Nordic power prices are heavily gas-correlated in low-hydro
    periods.
    """
    return [
        AccumulationTarget("GOLD", "GC=F", MarketType.COMMODITY),
        AccumulationTarget("SILVER", "SI=F", MarketType.COMMODITY),
        AccumulationTarget("CRUDE_OIL", "CL=F", MarketType.COMMODITY),
        AccumulationTarget(
            "NATURAL_GAS", "NG=F", MarketType.ENERGY,
            context_signal=SwedishElectricityContextSignal(zone="SE3"),
        ),
    ]
