"""
The Ghost Book strategy, frozen.

One place that defines the traded signal, so research, paper trading and any
future live run cannot drift apart.

SIGNAL
    For each coin, reconstruct the entry-price distribution of its open
    perpetual interest, take the notional-weighted average entry price (the
    cost basis), and compare it with a plain moving average of price:

        overhang(n) = ln(cost_basis) - ln(SMA_n(price))
        score(n)    = -overhang(n) / (sigma * sqrt(n))
        signal      = mean over n in {72h, 168h} of the cross-sectional
                      rank-normalised score(n)

    A positive overhang means the crowd's positions were opened above where
    price typically traded, so the book carries underwater longs that have to
    be unwound. Short those; buy the mirror image.

PORTFOLIO
    Cross-sectionally rank the signal over the liquid universe, weight linearly
    in rank, scale by inverse volatility, demean to dollar-neutral, cap each
    name, rebalance daily.

WHY IT SHOULD KEEP WORKING
    The edge is forced flow, not a forecast. Positions opened above the market
    are unwound because margin requires it, not because anyone chose to. The
    reason it is not already arbitraged away is that the input has to be
    stitched together from about 400,000 daily dump files; there is no feed for
    it and no exchange publishes it.

CAPACITY AND LIMITS
    Sized for a five-to-six-figure book across roughly 120 perpetuals. The
    signal is slow, so a one-day-stale input costs little; that is measured in
    the lag test and is what makes daily batch operation acceptable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .backtest import BTConfig, CostModel
from .positionmap import MapConfig

LOOKBACKS = (72, 168)


@dataclass(frozen=True)
class GhostBookSpec:
    """Everything that defines the traded strategy."""
    lookbacks: tuple[int, ...] = LOOKBACKS
    rebal_h: int = 24
    min_liq_usd: float = 20e6
    max_names: int = 120
    max_weight: float = 0.06
    gross: float = 1.0
    vol_scale: bool = True
    neutral: bool = True
    taker_bps: float = 5.0
    spread_bps: float = 3.0
    warmup_days: int = 75
    map_cfg: MapConfig = field(default_factory=lambda: MapConfig(checkpoint_min=60))

    def bt_config(self, capital_usd: float = 100_000.0) -> BTConfig:
        return BTConfig(rebal_h=self.rebal_h, scheme="rank", gross=self.gross,
                        max_weight=self.max_weight, vol_scale=self.vol_scale,
                        neutral=self.neutral, capital_usd=capital_usd,
                        cost=CostModel(taker_bps=self.taker_bps,
                                       spread_bps=self.spread_bps))

    def describe(self) -> dict:
        d = asdict(self)
        d["map_cfg"] = asdict(self.map_cfg)
        return d


SPEC = GhostBookSpec()


def overhang_scores(book: pd.DataFrame, close: pd.Series,
                    lookbacks=LOOKBACKS) -> pd.DataFrame:
    """Per-symbol raw scores from a reconstruction and its hourly closes.

    `book` is the output of positionmap.reconstruct (needs `time`, `cost_basis`).
    `close` is an hourly close series indexed by time.
    """
    out = pd.DataFrame({"time": pd.to_datetime(book["time"]).dt.floor("h")})
    idx = pd.DatetimeIndex(out["time"])
    ln_cb = np.log(book["cost_basis"].to_numpy(float))
    lr = np.log(close).diff()
    for n in lookbacks:
        ma = close.rolling(n, min_periods=n // 2).mean()
        ln_ma = np.log(ma).reindex(idx).to_numpy()
        sd = lr.rolling(n, min_periods=n // 2).std().reindex(idx).to_numpy()
        oh = ln_cb - ln_ma
        with np.errstate(divide="ignore", invalid="ignore"):
            out[f"score_{n}"] = np.where(sd > 0, -oh / (sd * np.sqrt(n)), np.nan)
    return out


def _normal_scores(s: pd.Series) -> pd.Series:
    """Rank-normalise to standard-normal scores within one cross-section."""
    from scipy.special import erfinv
    n = s.notna().sum()
    if n < 5:
        return pd.Series(np.nan, index=s.index)
    r = s.rank(pct=False)
    u = ((r - 0.5) / n).clip(1e-6, 1 - 1e-6)
    return pd.Series(np.sqrt(2.0) * erfinv(2 * u - 1), index=s.index).clip(-3, 3)


def combine(scores: pd.DataFrame, lookbacks=LOOKBACKS) -> pd.Series:
    """Cross-sectional ensemble across lookbacks for one timestamp.

    `scores` is indexed by symbol with one column per lookback.
    """
    parts = [_normal_scores(scores[f"score_{n}"]) for n in lookbacks
             if f"score_{n}" in scores.columns]
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts, axis=1).mean(axis=1)


def target_weights(signal: pd.Series, vol: pd.Series | None, spec: GhostBookSpec = SPEC
                   ) -> pd.Series:
    """Turn one cross-section of signal into the book to hold."""
    s = signal.dropna()
    if len(s) < 20:
        return pd.Series(dtype=float)

    r = s.rank(pct=True)
    w = (r - 0.5) * 2.0

    if spec.vol_scale and vol is not None:
        v = vol.reindex(w.index)
        med = v.median()
        if np.isfinite(med) and med > 0:
            w = w * (med / v).clip(lower=0.2, upper=3.0)

    w = w.dropna()
    if spec.neutral:
        w = w - w.mean()

    g = w.abs().sum()
    if g <= 0:
        return pd.Series(dtype=float)
    w = w / g * spec.gross
    w = w.clip(-spec.max_weight, spec.max_weight)
    g = w.abs().sum()
    return w / g * spec.gross if g > 0 else w
