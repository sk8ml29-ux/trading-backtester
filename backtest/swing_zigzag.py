"""Swing-point / zigzag detection for harmonic pattern strategies."""

from __future__ import annotations

import numpy as np
import pandas as pd


def detect_swing_points(
    high: pd.Series,
    low: pd.Series,
    lookback: int = 5,
) -> tuple[pd.Series, pd.Series]:
    """Local swing highs/lows: bar is swing if it's the extreme within lookback on each side."""
    n = lookback
    swing_hi = pd.Series(np.nan, index=high.index)
    swing_lo = pd.Series(np.nan, index=low.index)

    for i in range(n, len(high) - n):
        window_hi = high.iloc[i - n : i + n + 1]
        window_lo = low.iloc[i - n : i + n + 1]
        if high.iloc[i] >= window_hi.max():
            swing_hi.iloc[i] = high.iloc[i]
        if low.iloc[i] <= window_lo.min():
            swing_lo.iloc[i] = low.iloc[i]

    return swing_hi, swing_lo


def build_zigzag(
    high: pd.Series,
    low: pd.Series,
    lookback: int = 5,
    min_swing_pct: float = 0.001,
) -> list[tuple[int, float, str]]:
    """
    Build alternating zigzag pivots: [(bar_index, price, 'H'|'L'), ...].
    Filters out swings smaller than min_swing_pct of price.
    """
    swing_hi, swing_lo = detect_swing_points(high, low, lookback)
    pivots: list[tuple[int, float, str]] = []

    for i in range(len(high)):
        hi_val = swing_hi.iloc[i]
        lo_val = swing_lo.iloc[i]
        if not np.isnan(hi_val):
            pivots.append((i, float(hi_val), "H"))
        if not np.isnan(lo_val):
            pivots.append((i, float(lo_val), "L"))

    pivots.sort(key=lambda x: x[0])

    # Enforce alternation and minimum swing size
    cleaned: list[tuple[int, float, str]] = []
    for idx, price, kind in pivots:
        if not cleaned:
            cleaned.append((idx, price, kind))
            continue
        last_idx, last_price, last_kind = cleaned[-1]
        if kind == last_kind:
            # Keep more extreme pivot
            if kind == "H" and price > last_price:
                cleaned[-1] = (idx, price, kind)
            elif kind == "L" and price < last_price:
                cleaned[-1] = (idx, price, kind)
            continue
        move_pct = abs(price - last_price) / last_price
        if move_pct >= min_swing_pct:
            cleaned.append((idx, price, kind))

    return cleaned


def _ratio_ok(actual: float, target: float, tol: float) -> bool:
    if target <= 0:
        return False
    return abs(actual - target) / target <= tol


def check_gartley_bullish(x: float, a: float, b: float, c: float, d: float, tol: float = 0.15) -> bool:
    """Bullish Gartley: X=low, A=high, B, C, D=low (reversal)."""
    xa = a - x
    ab = a - b
    bc = c - b
    ad = a - d
    if xa <= 0 or ab <= 0 or bc <= 0 or ad <= 0:
        return False
    return (
        _ratio_ok(ab / xa, 0.618, tol)
        and 0.382 - tol <= bc / ab <= 0.886 + tol
        and _ratio_ok(ad / xa, 0.786, tol)
    )


def check_gartley_bearish(x: float, a: float, b: float, c: float, d: float, tol: float = 0.15) -> bool:
    """Bearish Gartley: X=high, A=low, B, C, D=high."""
    xa = x - a
    ab = b - a
    bc = b - c
    ad = d - a
    if xa <= 0 or ab <= 0 or bc <= 0 or ad <= 0:
        return False
    return (
        _ratio_ok(ab / xa, 0.618, tol)
        and 0.382 - tol <= bc / ab <= 0.886 + tol
        and _ratio_ok(ad / xa, 0.786, tol)
    )


def check_butterfly_bullish(x: float, a: float, b: float, c: float, d: float, tol: float = 0.18) -> bool:
    """Bullish Butterfly: D extends below X."""
    xa = a - x
    ab = a - b
    bc = c - b
    xd = x - d  # D below X
    if xa <= 0 or ab <= 0 or bc <= 0 or xd <= 0:
        return False
    return (
        _ratio_ok(ab / xa, 0.786, tol)
        and 0.382 - tol <= bc / ab <= 0.886 + tol
        and 1.27 - tol <= xd / xa <= 1.618 + tol
    )


def check_butterfly_bearish(x: float, a: float, b: float, c: float, d: float, tol: float = 0.18) -> bool:
    """Bearish Butterfly: D extends above X."""
    xa = x - a
    ab = b - a
    bc = b - c
    dx = d - x
    if xa <= 0 or ab <= 0 or bc <= 0 or dx <= 0:
        return False
    return (
        _ratio_ok(ab / xa, 0.786, tol)
        and 0.382 - tol <= bc / ab <= 0.886 + tol
        and 1.27 - tol <= dx / xa <= 1.618 + tol
    )


def scan_harmonic_signals(
    df: pd.DataFrame,
    lookback: int = 4,
    fib_tol: float = 0.15,
    patterns: tuple[str, ...] = ("gartley", "butterfly"),
) -> pd.DataFrame:
    """
    Precompute harmonic completion flags per bar.
    Adds columns: harmonic_long, harmonic_short, harmonic_reason.
    """
    out = df.copy()
    pivots = build_zigzag(out["high"], out["low"], lookback=lookback)
    n = len(out)
    long_sig = np.zeros(n, dtype=bool)
    short_sig = np.zeros(n, dtype=bool)
    reasons: list[str] = [""] * n

    if len(pivots) < 5:
        out["harmonic_long"] = long_sig
        out["harmonic_short"] = short_sig
        out["harmonic_reason"] = reasons
        return out

    for i in range(4, len(pivots)):
        p = pivots[i - 4 : i + 1]
        kinds = [x[2] for x in p]
        prices = [x[1] for x in p]
        bar_idx = p[-1][0]
        if bar_idx >= n:
            continue

        x, a, b, c, d = prices
        xk, ak, bk, ck, dk = kinds

        if xk == "L" and ak == "H" and bk == "L" and ck == "H" and dk == "L":
            if "gartley" in patterns and check_gartley_bullish(x, a, b, c, d, fib_tol):
                long_sig[bar_idx] = True
                reasons[bar_idx] = "gartley_bull"
            elif "butterfly" in patterns and check_butterfly_bullish(x, a, b, c, d, fib_tol):
                long_sig[bar_idx] = True
                reasons[bar_idx] = "butterfly_bull"

        if xk == "H" and ak == "L" and bk == "H" and ck == "L" and dk == "H":
            if "gartley" in patterns and check_gartley_bearish(x, a, b, c, d, fib_tol):
                short_sig[bar_idx] = True
                reasons[bar_idx] = "gartley_bear"
            elif "butterfly" in patterns and check_butterfly_bearish(x, a, b, c, d, fib_tol):
                short_sig[bar_idx] = True
                reasons[bar_idx] = "butterfly_bear"

    out["harmonic_long"] = long_sig
    out["harmonic_short"] = short_sig
    out["harmonic_reason"] = reasons
    return out
