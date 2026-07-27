"""
Cross-sectional panel assembly and the point-in-time universe screen.

Turns per-symbol reconstructions into one tidy (time, symbol) frame with
features, tradeable prices and forward returns, under strict causality:

    features at t  ->  execute at the OPEN of t+1  ->  hold k hours

The universe is re-selected at every rebalance from trailing liquidity only, so
coins enter when they become liquid and drop out when they die. The candidate
pool itself is every USDT perp Binance ever published metrics for, including
delisted ones, which is what keeps survivorship bias out of the study.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .vision_bulk import CACHE

FEATURES = ["tli", "frac_uw", "disp", "fuel_dn", "fuel_up", "oi_vel", "oi_usd_vel",
            "acct_ls", "tt_pos_ls", "tt_acct_ls", "taker_ls"]


def load_klines(symbol: str) -> pd.DataFrame:
    p = CACHE / f"kl1h_{symbol}.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def load_funding(symbol: str) -> pd.DataFrame:
    p = CACHE / f"fund_{symbol}.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def liquidity_series(kl: pd.DataFrame, window_days: int = 30) -> pd.Series:
    """Trailing median daily dollar volume, hourly, shifted to stay causal."""
    if kl.empty:
        return pd.Series(dtype=float)
    s = kl.set_index("time")["quote_volume"].astype(float)
    daily = s.rolling(24, min_periods=12).sum()
    return daily.rolling(24 * window_days, min_periods=24 * 5).median().shift(1)


def build_panel(maps: dict[str, pd.DataFrame], horizon_h: int = 8,
                verbose: bool = True) -> pd.DataFrame:
    """One row per (time, symbol) with features, execution prices and fwd return."""
    rows = []
    for i, (sym, m) in enumerate(maps.items(), 1):
        kl = load_klines(sym)
        if kl.empty or m.empty:
            continue
        kl = kl.sort_values("time").reset_index(drop=True)
        k = kl.set_index("time")

        # Execution happens at the open of the next hourly bar after the signal.
        exec_px = k["open"].shift(-1)
        exec_px.index = exec_px.index  # open of t+1, indexed at t
        fwd_px = k["open"].shift(-1 - horizon_h)

        liq = liquidity_series(kl)

        df = m.copy()
        df["time"] = pd.to_datetime(df["time"]).dt.floor("h")
        df = df.drop_duplicates("time", keep="last").set_index("time")

        df["exec_px"] = exec_px.reindex(df.index)
        df["fwd_px"] = fwd_px.reindex(df.index)
        df["liq_usd"] = liq.reindex(df.index)
        df["close"] = k["close"].reindex(df.index)

        df = df.dropna(subset=["exec_px", "liq_usd"])
        if df.empty:
            continue
        df["fwd_ret"] = df["fwd_px"] / df["exec_px"] - 1.0
        df["symbol"] = sym
        rows.append(df.reset_index())

        if verbose and i % 25 == 0:
            print(f"  panel {i}/{len(maps)}", flush=True)

    if not rows:
        return pd.DataFrame()
    panel = pd.concat(rows, ignore_index=True)
    return panel.sort_values(["time", "symbol"]).reset_index(drop=True)


def apply_universe(panel: pd.DataFrame, min_liq_usd: float = 20e6,
                   max_names: int = 120) -> pd.DataFrame:
    """Keep, at each timestamp, the most liquid names above an absolute floor."""
    out = panel[panel["liq_usd"] >= min_liq_usd].copy()
    if max_names and max_names > 0:
        out["_rank"] = out.groupby("time")["liq_usd"].rank(ascending=False, method="first")
        out = out[out["_rank"] <= max_names].drop(columns="_rank")
    return out.reset_index(drop=True)


def cross_sectional_z(panel: pd.DataFrame, cols: list[str], clip: float = 3.0,
                      min_names: int = 15) -> pd.DataFrame:
    """Rank-normalise each feature within each timestamp.

    Cross-sectional ranking removes the market-wide component and any drift in
    a feature's absolute level, so a signal built from these is dollar-neutral
    by construction and cannot ride a directional beta.
    """
    out = panel.copy()
    g = out.groupby("time")
    counts = g["symbol"].transform("size")
    out = out[counts >= min_names].copy()
    if out.empty:
        return out
    g = out.groupby("time")
    for c in cols:
        if c not in out.columns:
            continue
        r = g[c].rank(pct=True)
        n = g[c].transform("count")
        z = np.sqrt(2.0) * _erfinv(2.0 * ((r * n - 0.5) / n).clip(1e-6, 1 - 1e-6) - 1.0)
        out[f"z_{c}"] = np.clip(z, -clip, clip)
    return out


def _erfinv(x):
    from scipy.special import erfinv
    return erfinv(x)


def forward_return_grid(panel: pd.DataFrame, horizons: tuple[int, ...],
                        maps: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """Attach several forward-return horizons for the information-decay study."""
    out = panel.copy()
    for h in horizons:
        col = f"fwd_{h}h"
        vals = []
        for sym, g in out.groupby("symbol", sort=False):
            kl = load_klines(sym)
            if kl.empty:
                vals.append(pd.Series(np.nan, index=g.index))
                continue
            k = kl.set_index("time")["open"]
            ex = k.shift(-1).reindex(g["time"].values)
            fw = k.shift(-1 - h).reindex(g["time"].values)
            vals.append(pd.Series((fw.values / ex.values) - 1.0, index=g.index))
        out[col] = pd.concat(vals).sort_index()
    return out
