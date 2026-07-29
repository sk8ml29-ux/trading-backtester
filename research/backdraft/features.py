"""
Minute-level features describing forced selling and its exhaustion.

The thesis is about liquidity, not information. When leveraged positions are
margin-called, the resulting orders have to be filled regardless of price, so
the move overshoots and then unwinds once the forced seller is finished. Three
independent instruments measure that, and all three are public:

  ORDER FLOW      the taker-buy split inside a 1m kline gives the share of
                  volume that was aggressive buying, so a deeply negative
                  imbalance is selling that demanded immediate liquidity

  BASIS           the premium index prices the perpetual against the spot
                  index. A sharp negative print means the perp is being dumped
                  faster than spot can follow, which is the signature of
                  position unwinding rather than a repricing of the asset

  PARTICIPATION   trade count and volume relative to normal. Liquidation flow
                  arrives as a burst of many orders, not one large print

Exhaustion is what the strategy actually waits for: the move is extreme, but
order flow has stopped getting worse and the basis has started to close.

Every statistic is trailing and shifted, so nothing here can see its own future.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_PER_DAY = 1440
VOL_WINDOW = 7 * MIN_PER_DAY          # trailing window for scaling statistics
MIN_OBS = 2000


def _safe_div(a, b):
    return np.where(np.abs(b) > 0, a / np.where(np.abs(b) > 0, b, 1.0), np.nan)


def build(perp: pd.DataFrame, premium: pd.DataFrame, spot: pd.DataFrame,
          horizon_min: tuple[int, ...] = (5, 15, 30, 60, 120, 240),
          drop_win: int = 15) -> pd.DataFrame:
    """One row per minute with stress features and forward spot returns."""
    if perp.empty or len(perp) < VOL_WINDOW:
        return pd.DataFrame()

    df = perp.sort_values("time").reset_index(drop=True).copy()
    if not premium.empty:
        df = df.merge(premium, on="time", how="left")
    else:
        for c in ["prem_open", "prem_high", "prem_low", "prem_close"]:
            df[c] = np.nan
    if not spot.empty:
        df = df.merge(spot, on="time", how="left")
    else:
        for c in ["s_open", "s_high", "s_low", "s_close", "s_qvol", "s_tbq"]:
            df[c] = np.nan

    c = df["close"].astype(float)
    qv = df["quote_volume"].astype(float)
    tbq = df["taker_buy_quote"].astype(float)
    cnt = df["count"].astype(float)

    # --- magnitude of the move -------------------------------------------
    ln = np.log(c.replace(0, np.nan))
    df["r_drop"] = ln - ln.shift(drop_win)
    sig = df["r_drop"].rolling(VOL_WINDOW, min_periods=MIN_OBS).std().shift(drop_win)
    df["sigma"] = sig
    df["z_drop"] = _safe_div(df["r_drop"], sig)

    # --- order flow -------------------------------------------------------
    # Signed aggressive flow: +1 all buying, -1 all selling.
    df["ofi_1m"] = _safe_div(2.0 * tbq - qv, qv)
    roll_q = qv.rolling(drop_win, min_periods=drop_win).sum()
    roll_b = tbq.rolling(drop_win, min_periods=drop_win).sum()
    df["ofi"] = _safe_div(2.0 * roll_b - roll_q, roll_q)
    # Aggressive selling measured against this symbol's own normal flow.
    sell_usd = roll_q - roll_b
    norm_sell = sell_usd.rolling(VOL_WINDOW, min_periods=MIN_OBS).median().shift(drop_win)
    df["sell_burst"] = _safe_div(sell_usd, norm_sell)

    # --- participation ----------------------------------------------------
    norm_q = qv.rolling(VOL_WINDOW, min_periods=MIN_OBS).median().shift(1)
    df["vol_burst"] = _safe_div(qv, norm_q)
    norm_c = cnt.rolling(VOL_WINDOW, min_periods=MIN_OBS).median().shift(1)
    df["cnt_burst"] = _safe_div(cnt, norm_c)

    # --- basis ------------------------------------------------------------
    prem = df["prem_close"].astype(float)
    psig = prem.rolling(VOL_WINDOW, min_periods=MIN_OBS).std().shift(1)
    df["prem"] = prem
    df["prem_z"] = _safe_div(prem, psig)
    df["prem_min"] = prem.rolling(drop_win, min_periods=1).min()
    df["prem_z_min"] = _safe_div(df["prem_min"], psig)
    # Has the basis started to close since its worst point in the window?
    df["prem_recover"] = _safe_div(prem - df["prem_min"], psig)

    # --- exhaustion -------------------------------------------------------
    # Selling pressure easing relative to the worst minute of the window.
    df["ofi_min"] = df["ofi_1m"].rolling(drop_win, min_periods=1).min()
    df["ofi_recover"] = df["ofi_1m"] - df["ofi_min"]
    # Position of the current price inside the window's range: near the low
    # means still falling, off the low means buyers have shown up.
    lo = c.rolling(drop_win, min_periods=drop_win).min()
    hi = c.rolling(drop_win, min_periods=drop_win).max()
    df["range_pos"] = _safe_div(c - lo, hi - lo)

    # --- context ----------------------------------------------------------
    df["dollar_vol_24h"] = qv.rolling(MIN_PER_DAY, min_periods=200).sum().shift(1)
    ln_s = np.log(df["s_close"].replace(0, np.nan)) if "s_close" in df else pd.Series(np.nan, index=df.index)
    df["spot_ok"] = df["s_open"].notna() & df["s_close"].notna()

    # --- forward returns on the traded leg (spot), entered next minute -----
    s_open = df["s_open"].astype(float)
    entry = s_open.shift(-1)
    df["entry_px"] = entry
    for h in horizon_min:
        df[f"fwd_{h}m"] = _safe_div(s_open.shift(-1 - h) - entry, entry)
    # Perp reference for comparison, same convention.
    p_open = df["open"].astype(float)
    p_entry = p_open.shift(-1)
    for h in horizon_min:
        df[f"pfwd_{h}m"] = _safe_div(p_open.shift(-1 - h) - p_entry, p_entry)

    # Worst excursion after entry, which is what decides whether a stop survives.
    low_fwd = df["s_low"].astype(float).shift(-1).rolling(60, min_periods=1).min()
    df["mae_60m"] = _safe_div(low_fwd - entry, entry)
    del ln_s
    return df
