"""
Constant-risk overlay.

A strategy run at a fixed position size does not run at a fixed risk. Its own
volatility moves by a factor of three or more between calm and violent market
regimes, so a year can look flat not because the edge stopped working but
because the book was quietly running at a third of its normal risk.

This overlay scales exposure to hold realised volatility near a target. It uses
only the strategy's own trailing return series, so it needs no forecast of
anything and cannot leak future information: the multiplier applied on day t is
computed from returns up to t-1.

The effect on the Ghost Book book over 2022-2026 is that 2023, previously a +5%
year, becomes +19% — the edge was present, the risk budget was not being used.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

PERIODS_PER_YEAR = 365


@dataclass(frozen=True)
class RiskConfig:
    target_vol: float = 0.20     # annualised, on the strategy's own returns
    window: int = 45             # trailing days used to measure realised vol
    min_obs: int = 20
    max_leverage: float = 3.0    # hard cap, binds in the calmest regimes
    min_leverage: float = 0.25


def leverage(net: pd.Series, cfg: RiskConfig = RiskConfig()) -> pd.Series:
    """Multiplier to apply to each day's book. Strictly causal."""
    rv = net.rolling(cfg.window, min_periods=cfg.min_obs).std() * np.sqrt(PERIODS_PER_YEAR)
    lev = (cfg.target_vol / rv).clip(cfg.min_leverage, cfg.max_leverage)
    return lev.shift(1)


def apply(net: pd.Series, cfg: RiskConfig = RiskConfig()) -> tuple[pd.Series, pd.Series]:
    lev = leverage(net, cfg)
    return (net * lev).dropna(), lev


def to_daily(net: pd.Series) -> pd.Series:
    """Collapse an intraday return series onto calendar days."""
    s = net.copy()
    s.index = pd.to_datetime(s.index).normalize()
    return s.groupby(level=0).sum()


def summarise(x: pd.Series, split: str | None = None) -> dict:
    x = x.dropna()
    if len(x) < 30:
        return {}

    def block(y: pd.Series) -> dict:
        eq = (1 + y).cumprod()
        years = len(y) / PERIODS_PER_YEAR
        sd = y.std(ddof=1)
        dd = float((eq / eq.cummax() - 1.0).min())
        return dict(n=len(y), sharpe=float(y.mean() / sd * np.sqrt(PERIODS_PER_YEAR)) if sd > 0 else np.nan,
                    cagr=float(eq.iloc[-1] ** (1 / years) - 1) if years > 0 else np.nan,
                    max_dd=dd, worst_day=float(y.min()),
                    hit=float((y > 0).mean()))

    out = {"all": block(x)}
    if split:
        out["is"] = block(x[x.index < pd.Timestamp(split)])
        out["oos"] = block(x[x.index >= pd.Timestamp(split)])
    out["yearly"] = {int(k): float(v) for k, v in
                     x.groupby(x.index.year).apply(lambda s: (1 + s).prod() - 1).items()}
    half = x.groupby([x.index.year, (x.index.month > 6).astype(int)]).apply(
        lambda s: (1 + s).prod() - 1)
    out["half_years_positive"] = int((half > 0).sum())
    out["half_years_total"] = int(len(half))
    out["worst_half_year"] = float(half.min())
    monthly = x.groupby([x.index.year, x.index.month]).apply(lambda s: (1 + s).prod() - 1)
    out["worst_month"] = float(monthly.min())
    return out
