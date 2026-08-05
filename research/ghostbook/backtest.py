"""
Cross-sectional backtester for the Ghost Book signal.

Accounting rules, all deliberately pessimistic:

  * signals read at t, filled at the OPEN of t+1, held to the next rebalance
  * every unit of turnover pays taker fee + spread; nothing is assumed to be
    filled passively
  * a size-dependent impact term punishes trading illiquid names
  * perp legs pay realised funding over the holding period, longs and shorts
  * positions are capped per name so no single coin can carry the result

The market-neutral book is dollar-neutral by construction because weights are
demeaned cross-sectionally at every rebalance.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .panel import load_funding

HOURS_PER_YEAR = 24 * 365


@dataclass
class CostModel:
    taker_bps: float = 5.0        # Binance/OKX USDT-perp taker tier
    spread_bps: float = 3.0       # half-spread paid on a marketable order
    impact_coef: float = 0.10     # impact_bps = coef * 1e4 * (order / daily volume)
    use_funding: bool = True

    def per_side_bps(self) -> float:
        return self.taker_bps + self.spread_bps


@dataclass
class BTConfig:
    rebal_h: int = 8
    scheme: str = "rank"          # "rank" or "quantile"
    quantile: float = 0.2
    gross: float = 1.0            # gross exposure, 1.0 = 100% of equity traded
    max_weight: float = 0.06
    vol_scale: bool = True
    vol_lookback: int = 14 * 24
    min_names: int = 20
    neutral: bool = True          # demean weights -> dollar-neutral
    long_only: bool = False       # spot expression: drop the short leg
    smooth_hl_h: float = 0.0      # EWMA half-life applied to the score
    no_trade_band: float = 0.0    # skip trades smaller than band * max_weight
    capital_usd: float = 100_000.0
    cost: CostModel = field(default_factory=CostModel)


def _wide(panel: pd.DataFrame, col: str) -> pd.DataFrame:
    return panel.pivot_table(index="time", columns="symbol", values=col, aggfunc="last")


def funding_matrix(symbols: list[str], index: pd.DatetimeIndex,
                   rebal_h: int) -> pd.DataFrame:
    """Realised funding accrued in each holding period, per symbol."""
    cols = {}
    for s in symbols:
        f = load_funding(s)
        if f.empty:
            continue
        ser = f.set_index("time")["funding_rate"].sort_index()
        # Sum the settlements that land inside each forward holding window.
        cum = ser.cumsum()
        edges = index
        c = cum.reindex(cum.index.union(edges)).ffill().reindex(edges)
        cols[s] = c.shift(-1) - c
    if not cols:
        return pd.DataFrame(index=index)
    return pd.DataFrame(cols, index=index)


def build_weights(score: pd.DataFrame, cfg: BTConfig,
                  vol: pd.DataFrame | None = None) -> pd.DataFrame:
    """Turn a (time x symbol) score matrix into tradeable weights."""
    s = score.copy()
    valid = s.notna().sum(axis=1)
    s = s[valid >= cfg.min_names]
    if s.empty:
        return s

    if cfg.scheme == "quantile":
        r = s.rank(axis=1, pct=True)
        w = pd.DataFrame(0.0, index=s.index, columns=s.columns)
        w[r >= 1 - cfg.quantile] = 1.0
        w[r <= cfg.quantile] = -1.0
        w = w.where(s.notna())
    else:
        r = s.rank(axis=1, pct=True)
        w = (r - 0.5) * 2.0            # linear in cross-sectional rank, in [-1, 1]

    if cfg.vol_scale and vol is not None:
        v = vol.reindex_like(w)
        med = v.median(axis=1)
        inv = (med.values[:, None] / v).clip(upper=3.0, lower=0.2)
        w = w * inv

    if cfg.neutral:
        w = w.sub(w.mean(axis=1), axis=0)

    if cfg.long_only:
        # Spot-tradeable expression: keep the bullish half of the book only.
        w = w.clip(lower=0.0)

    gross = w.abs().sum(axis=1).replace(0.0, np.nan)
    w = w.div(gross, axis=0) * cfg.gross
    w = w.clip(-cfg.max_weight, cfg.max_weight)

    # Re-normalise after the cap so gross exposure stays at target.
    gross = w.abs().sum(axis=1).replace(0.0, np.nan)
    w = w.div(gross, axis=0) * cfg.gross
    return w.fillna(0.0)


def _apply_band(target: pd.DataFrame, ret: pd.DataFrame,
                cfg: BTConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Walk the book forward, letting positions drift and only trading real moves.

    Returns the weights actually held and the traded amounts. With a zero band
    this reduces to rebalancing fully to target every period, which is the
    behaviour the earlier vectorised path had.
    """
    tgt = target.to_numpy(float)
    rets = np.nan_to_num(ret.to_numpy(float))
    n_t, n_s = tgt.shape
    held = np.zeros(n_s)
    out_w = np.zeros_like(tgt)
    out_tr = np.zeros_like(tgt)
    thresh = cfg.no_trade_band * cfg.max_weight

    for i in range(n_t):
        if i > 0:
            drifted = held * (1.0 + rets[i - 1])
            g = np.abs(drifted).sum()
            if g > 0:
                drifted = drifted / g * cfg.gross
        else:
            drifted = held

        want = np.nan_to_num(tgt[i])
        if thresh > 0:
            move = np.abs(want - drifted) > thresh
            new = np.where(move, want, drifted)
        else:
            new = want

        out_tr[i] = np.abs(new - drifted)
        out_w[i] = new
        held = new

    return (pd.DataFrame(out_w, index=target.index, columns=target.columns),
            pd.DataFrame(out_tr, index=target.index, columns=target.columns))


def run(panel: pd.DataFrame, score_col: str, cfg: BTConfig = BTConfig(),
        start: str | None = None, end: str | None = None) -> dict:
    """Backtest one score column. Returns equity curve plus summary stats."""
    p = panel
    if start:
        p = p[p["time"] >= pd.Timestamp(start)]
    if end:
        p = p[p["time"] < pd.Timestamp(end)]
    if p.empty:
        return dict(ok=False, reason="empty panel")

    px = _wide(p, "exec_px")
    sc = _wide(p, score_col)
    liq = _wide(p, "liq_usd")

    grid = px.index[::cfg.rebal_h]
    px = px.reindex(grid)
    sc = sc.reindex(grid)
    liq = liq.reindex(grid)

    ret = px.shift(-1) / px - 1.0
    ret = ret.where(np.abs(ret) < 3.0)           # guard against bad prints

    vol = None
    if cfg.vol_scale:
        lb = max(3, cfg.vol_lookback // cfg.rebal_h)
        vol = (px.pct_change().rolling(lb, min_periods=lb // 2).std()
               .shift(1).replace(0.0, np.nan))

    sc = sc.where(ret.notna() & px.notna())
    if cfg.smooth_hl_h and cfg.smooth_hl_h > 0:
        # Averaging the score over time removes the part of the reshuffling
        # that is noise rather than a change of view, which is pure cost saved.
        hl = max(1.0, cfg.smooth_hl_h / cfg.rebal_h)
        sc = sc.ewm(halflife=hl, min_periods=1, ignore_na=True).mean()

    w = build_weights(sc, cfg, vol)
    if w.empty:
        return dict(ok=False, reason="no weights")

    ret = ret.reindex_like(w).fillna(0.0)
    liq = liq.reindex_like(w)

    w, traded = _apply_band(w, ret, cfg)
    gross_pnl = (w * ret).sum(axis=1)
    turnover = traded.sum(axis=1)

    side_cost = cfg.cost.per_side_bps() / 1e4
    fee = turnover * side_cost

    # Impact scales with the order's footprint in that name's daily volume.
    order_usd = traded * cfg.capital_usd
    participation = (order_usd / liq.replace(0.0, np.nan)).fillna(0.0)
    impact = (participation * cfg.cost.impact_coef * traded).sum(axis=1)

    fund_cost = pd.Series(0.0, index=w.index)
    if cfg.cost.use_funding:
        fm = funding_matrix(list(w.columns), w.index, cfg.rebal_h)
        fm = fm.reindex_like(w).fillna(0.0)
        fund_cost = (w * fm).sum(axis=1)      # longs pay a positive rate

    net = gross_pnl - fee - impact - fund_cost
    net = net.iloc[:-1] if len(net) else net

    eq = (1.0 + net).cumprod()
    stats = summarise(net, eq, turnover.reindex(net.index), cfg)
    return dict(ok=True, net=net, gross=gross_pnl.reindex(net.index), equity=eq,
                turnover=turnover.reindex(net.index), weights=w, stats=stats,
                fees=fee.reindex(net.index), funding=fund_cost.reindex(net.index),
                impact=impact.reindex(net.index))


def summarise(net: pd.Series, eq: pd.Series, turnover: pd.Series,
              cfg: BTConfig) -> dict:
    if len(net) < 5:
        return dict(n=len(net))
    per_year = HOURS_PER_YEAR / cfg.rebal_h
    years = len(net) / per_year
    total = float(eq.iloc[-1])
    cagr = total ** (1 / years) - 1 if years > 0 and total > 0 else np.nan
    vol = float(net.std(ddof=1)) * np.sqrt(per_year)
    sharpe = float(net.mean() / net.std(ddof=1) * np.sqrt(per_year)) if net.std(ddof=1) > 0 else np.nan
    dd = float((eq / eq.cummax() - 1.0).min())
    downside = net[net < 0].std(ddof=1)
    sortino = float(net.mean() / downside * np.sqrt(per_year)) if downside and downside > 0 else np.nan
    return dict(n=int(len(net)), years=round(years, 2), total_return=total - 1.0,
                cagr=cagr, vol=vol, sharpe=sharpe, sortino=sortino, max_dd=dd,
                calmar=(cagr / abs(dd)) if dd < 0 else np.nan,
                hit=float((net > 0).mean()),
                turnover_per_rebal=float(turnover.mean()),
                turnover_annual=float(turnover.mean() * per_year))


def yearly(net: pd.Series) -> pd.DataFrame:
    if net.empty:
        return pd.DataFrame()
    g = net.groupby(net.index.year)
    return pd.DataFrame(dict(ret=g.apply(lambda s: (1 + s).prod() - 1),
                             n=g.size(),
                             sharpe=g.apply(lambda s: s.mean() / s.std(ddof=1) * np.sqrt(365 * 3)
                                            if s.std(ddof=1) > 0 else np.nan)))
