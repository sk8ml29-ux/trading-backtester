"""
Cross-sectional backtester for the bar-matrix panel.

Timing is deliberately conservative: a signal that uses data up to the close of
bar t is filled at the OPEN of bar t+1 and held to the open of the bar after
the next rebalance. Costs are charged on every unit of turnover at taker rates;
nothing is assumed to rest passively.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Cost:
    taker_bps: float = 5.0
    spread_bps: float = 3.0

    @property
    def per_side(self) -> float:
        return (self.taker_bps + self.spread_bps) / 1e4


@dataclass
class Config:
    hold_bars: int = 4           # rebalance cadence, in bars
    gross: float = 1.0
    max_weight: float = 0.10
    min_names: int = 12
    vol_scale: bool = True
    vol_window: int = 24 * 14
    neutral: bool = True
    scheme: str = "rank"         # "rank" or "quantile"
    quantile: float = 0.25
    cost: Cost = field(default_factory=Cost)


def weights_from_score(score: pd.DataFrame, cfg: Config,
                       vol: pd.DataFrame | None) -> pd.DataFrame:
    s = score.copy()
    s = s[s.notna().sum(axis=1) >= cfg.min_names]
    if s.empty:
        return s
    if cfg.scheme == "quantile":
        r = s.rank(axis=1, pct=True)
        w = pd.DataFrame(0.0, index=s.index, columns=s.columns)
        w[r >= 1 - cfg.quantile] = 1.0
        w[r <= cfg.quantile] = -1.0
        w = w.where(s.notna())
    else:
        w = (s.rank(axis=1, pct=True) - 0.5) * 2.0

    if cfg.vol_scale and vol is not None:
        v = vol.reindex_like(w)
        med = v.median(axis=1)
        w = w * (med.values[:, None] / v).clip(0.2, 3.0)

    if cfg.neutral:
        w = w.sub(w.mean(axis=1), axis=0)
    g = w.abs().sum(axis=1).replace(0.0, np.nan)
    w = w.div(g, axis=0) * cfg.gross
    w = w.clip(-cfg.max_weight, cfg.max_weight)
    g = w.abs().sum(axis=1).replace(0.0, np.nan)
    return (w.div(g, axis=0) * cfg.gross).fillna(0.0)


def run(score: pd.DataFrame, open_px: pd.DataFrame, cfg: Config = Config(),
        bar_min: int = 60, start: str | None = None,
        end: str | None = None) -> dict:
    idx = score.index
    if start:
        idx = idx[idx >= pd.Timestamp(start)]
    if end:
        idx = idx[idx < pd.Timestamp(end)]
    if len(idx) < 50:
        return dict(ok=False, reason="too few bars")

    sc = score.reindex(idx)
    op = open_px.reindex(idx)

    # Rebalance grid; execution happens one bar after the signal.
    grid = idx[::cfg.hold_bars]
    entry = op.shift(-1).reindex(grid)
    exit_ = op.shift(-1 - cfg.hold_bars).reindex(grid)
    ret = (exit_ / entry - 1.0)
    ret = ret.where(np.abs(ret) < 2.0)

    vol = None
    if cfg.vol_scale:
        lb = max(3, cfg.vol_window // cfg.hold_bars)
        vol = (op.pct_change().rolling(cfg.vol_window, min_periods=cfg.vol_window // 3)
               .std().shift(1).reindex(grid).replace(0.0, np.nan))

    sc_g = sc.reindex(grid).where(ret.notna() & entry.notna())
    w = weights_from_score(sc_g, cfg, vol)
    if w.empty:
        return dict(ok=False, reason="no weights")
    ret = ret.reindex_like(w).fillna(0.0)

    gross_pnl = (w * ret).sum(axis=1)

    drift = w.shift(1).fillna(0.0) * (1.0 + ret.shift(1).fillna(0.0))
    dg = drift.abs().sum(axis=1).replace(0.0, np.nan)
    drift = drift.div(dg, axis=0).fillna(0.0) * cfg.gross
    turnover = (w - drift).abs().sum(axis=1)

    net = gross_pnl - turnover * cfg.cost.per_side
    net = net.iloc[:-1]
    eq = (1.0 + net).cumprod()

    per_year = (365 * 24 * 60) / (bar_min * cfg.hold_bars)
    stats = summarise(net, eq, turnover.reindex(net.index), per_year)
    stats["gross_sharpe"] = float(gross_pnl.reindex(net.index).mean() /
                                  gross_pnl.reindex(net.index).std(ddof=1) *
                                  np.sqrt(per_year))
    return dict(ok=True, net=net, gross=gross_pnl.reindex(net.index), equity=eq,
                turnover=turnover.reindex(net.index), weights=w, stats=stats)


def summarise(net: pd.Series, eq: pd.Series, turnover: pd.Series,
              per_year: float) -> dict:
    if len(net) < 10:
        return dict(n=len(net))
    years = len(net) / per_year
    total = float(eq.iloc[-1])
    sd = net.std(ddof=1)
    dd = float((eq / eq.cummax() - 1.0).min())
    cagr = total ** (1 / years) - 1 if years > 0 and total > 0 else np.nan
    down = net[net < 0].std(ddof=1)
    return dict(n=int(len(net)), years=round(years, 2), total_return=total - 1.0,
                cagr=cagr, vol=float(sd * np.sqrt(per_year)),
                sharpe=float(net.mean() / sd * np.sqrt(per_year)) if sd > 0 else np.nan,
                sortino=float(net.mean() / down * np.sqrt(per_year)) if down and down > 0 else np.nan,
                max_dd=dd, calmar=(cagr / abs(dd)) if dd < 0 else np.nan,
                hit=float((net > 0).mean()),
                turnover_per_rebal=float(turnover.mean()),
                turnover_annual=float(turnover.mean() * per_year))


def yearly(net: pd.Series, per_year: float) -> pd.DataFrame:
    if net.empty:
        return pd.DataFrame()
    g = net.groupby(net.index.year)
    return pd.DataFrame({
        "ret": g.apply(lambda s: (1 + s).prod() - 1),
        "n": g.size(),
        "sharpe": g.apply(lambda s: s.mean() / s.std(ddof=1) * np.sqrt(per_year)
                          if s.std(ddof=1) > 0 else np.nan),
    })
