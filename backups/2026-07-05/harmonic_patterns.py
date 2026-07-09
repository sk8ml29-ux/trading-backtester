"""XABCD harmonic pattern detection on swing pivots (Gartley, Bat, Butterfly, Crab)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

PatternName = Literal["gartley", "bat", "butterfly", "crab"]
Side = Literal["long", "short"]


@dataclass
class HarmonicMatch:
    pattern: PatternName
    side: Side
    d_price: float
    stop: float
    target: float
    x_idx: int
    d_idx: int


# Fibonacci ratio specs: (target, tolerance) or ((lo, hi), tolerance)
PATTERN_SPECS: dict[PatternName, dict] = {
    "gartley": {
        "ab_xa": (0.618, 0.10),
        "bc_ab": ((0.382, 0.886), 0.12),
        "cd_bc": ((1.272, 1.618), 0.15),
        "ad_xa": (0.786, 0.10),
        "bullish_d_below_a": True,
    },
    "bat": {
        "ab_xa": ((0.382, 0.50), 0.10),
        "bc_ab": ((0.382, 0.886), 0.12),
        "cd_bc": ((1.618, 2.618), 0.18),
        "ad_xa": (0.886, 0.10),
        "bullish_d_below_a": True,
    },
    "butterfly": {
        "ab_xa": (0.786, 0.10),
        "bc_ab": ((0.382, 0.886), 0.12),
        "cd_bc": ((1.618, 2.618), 0.18),
        "ad_xa": ((1.272, 1.618), 0.12),
        "bullish_d_below_x": True,
    },
    "crab": {
        "ab_xa": ((0.382, 0.618), 0.10),
        "bc_ab": ((0.382, 0.886), 0.12),
        "cd_bc": ((2.618, 3.618), 0.20),
        "ad_xa": (1.618, 0.12),
        "bullish_d_below_x": True,
    },
}


def _in_range(ratio: float, spec, tol: float = 0.0) -> bool:
    if isinstance(spec[0], tuple):
        lo, hi = spec[0]
        t = spec[1] if len(spec) > 1 else tol
        return (lo - t) <= ratio <= (hi + t)
    target, t = spec
    return abs(ratio - target) <= t


def find_pivots(high: np.ndarray, low: np.ndarray, left: int = 3, right: int = 3) -> list[tuple[int, float, bool]]:
    """Return (index, price, is_high) pivot list."""
    n = len(high)
    pivots: list[tuple[int, float, bool]] = []
    for i in range(left, n - right):
        window_h = high[i - left : i + right + 1]
        window_l = low[i - left : i + right + 1]
        if high[i] >= window_h.max() and high[i] > high[i - 1] and high[i] > high[i + 1]:
            pivots.append((i, float(high[i]), True))
        elif low[i] <= window_l.min() and low[i] < low[i - 1] and low[i] < low[i + 1]:
            pivots.append((i, float(low[i]), False))
    return pivots


def _check_bullish_xabcd(
    x: float, a: float, b: float, c: float, d: float, pattern: PatternName
) -> bool:
    if not (x < a and b < a and c > b and d < c):
        return False
    xa = a - x
    ab = a - b
    bc = c - b
    cd = c - d
    ad = a - d
    if xa <= 0 or ab <= 0 or bc <= 0 or cd <= 0:
        return False

    spec = PATTERN_SPECS[pattern]
    if not _in_range(ab / xa, spec["ab_xa"]):
        return False
    if not _in_range(bc / ab, spec["bc_ab"]):
        return False
    if not _in_range(cd / bc, spec["cd_bc"]):
        return False
    if not _in_range(ad / xa, spec["ad_xa"]):
        return False
    if spec.get("bullish_d_below_x") and d >= x:
        return False
    if spec.get("bullish_d_below_a") and d >= a:
        return False
    return True


def _check_bearish_xabcd(
    x: float, a: float, b: float, c: float, d: float, pattern: PatternName
) -> bool:
    if not (x > a and b > a and c < b and d > c):
        return False
    xa = x - a
    ab = b - a
    bc = b - c
    cd = d - c
    ad = d - a
    if xa <= 0 or ab <= 0 or bc <= 0 or cd <= 0:
        return False

    spec = PATTERN_SPECS[pattern]
    if not _in_range(ab / xa, spec["ab_xa"]):
        return False
    if not _in_range(bc / ab, spec["bc_ab"]):
        return False
    if not _in_range(cd / bc, spec["cd_bc"]):
        return False
    if not _in_range(ad / xa, spec["ad_xa"]):
        return False
    if spec.get("bullish_d_below_x") and d <= x:
        return False
    if spec.get("bullish_d_below_a") and d <= a:
        return False
    return True


def scan_harmonic_at_bar(
    pivots: list[tuple[int, float, bool]],
    bar_idx: int,
    close: float,
    atr: float,
    patterns: list[PatternName],
    pivot_lookback: int,
    d_tolerance_atr: float,
    reward_risk: float,
) -> HarmonicMatch | None:
    """Check if `bar_idx` completes a harmonic at D (price near computed D level)."""
    recent = [(i, p, h) for i, p, h in pivots if i <= bar_idx and i >= bar_idx - pivot_lookback]
    if len(recent) < 5:
        return None

    for start in range(len(recent) - 4):
        pts = recent[start : start + 5]
        if len(pts) < 5:
            continue
        (xi, xp, xh), (ai, ap, ah), (bi, bp, bh), (ci, cp, ch), (di, dp, dh) = pts
        if di != bar_idx and abs(di - bar_idx) > 2:
            continue

        for pat in patterns:
            # Bullish: X low, A high, B low, C high, D low
            if not xh and ah and not bh and ch and not dh:
                if _check_bullish_xabcd(xp, ap, bp, cp, dp, pat):
                    if abs(close - dp) <= atr * d_tolerance_atr:
                        stop = dp - atr * 0.5
                        risk = close - stop
                        if risk <= 0:
                            continue
                        return HarmonicMatch(
                            pattern=pat,
                            side="long",
                            d_price=dp,
                            stop=stop,
                            target=close + risk * reward_risk,
                            x_idx=xi,
                            d_idx=di,
                        )

            # Bearish: X high, A low, B high, C low, D high
            if xh and not ah and bh and not ch and dh:
                if _check_bearish_xabcd(xp, ap, bp, cp, dp, pat):
                    if abs(close - dp) <= atr * d_tolerance_atr:
                        stop = dp + atr * 0.5
                        risk = stop - close
                        if risk <= 0:
                            continue
                        return HarmonicMatch(
                            pattern=pat,
                            side="short",
                            d_price=dp,
                            stop=stop,
                            target=close - risk * reward_risk,
                            x_idx=xi,
                            d_idx=di,
                        )
    return None


def annotate_harmonic_signals(
    df: pd.DataFrame,
    pivot_left: int = 3,
    pivot_right: int = 3,
    pivot_lookback: int = 80,
    patterns: list[PatternName] | None = None,
    d_tolerance_atr: float = 0.35,
    reward_risk: float = 2.0,
) -> pd.DataFrame:
    """Add harmonic_side, harmonic_stop, harmonic_target, harmonic_pattern columns."""
    patterns = patterns or ["butterfly", "gartley", "bat"]
    out = df.copy()
    high = out["high"].to_numpy(dtype=float)
    low = out["low"].to_numpy(dtype=float)
    close = out["close"].to_numpy(dtype=float)
    atr = out["atr"].to_numpy(dtype=float) if "atr" in out.columns else np.full(len(out), np.nan)

    pivots = find_pivots(high, low, pivot_left, pivot_right)
    n = len(out)
    side_col = np.full(n, np.nan, dtype=object)
    stop_col = np.full(n, np.nan)
    target_col = np.full(n, np.nan)
    pat_col = np.full(n, np.nan, dtype=object)

    for start in range(len(pivots) - 4):
        pts = pivots[start : start + 5]
        (xi, xp, xh), (ai, ap, ah), (bi, bp, bh), (ci, cp, ch), (di, dp, dh) = pts
        if di >= n or np.isnan(atr[di]) or atr[di] <= 0:
            continue

        for pat in patterns:
            if not xh and ah and not bh and ch and not dh:
                if _check_bullish_xabcd(xp, ap, bp, cp, dp, pat):
                    entry = close[di]
                    if abs(entry - dp) <= atr[di] * d_tolerance_atr:
                        stop = dp - atr[di] * 0.5
                        risk = entry - stop
                        if risk > 0:
                            side_col[di] = "long"
                            stop_col[di] = stop
                            target_col[di] = entry + risk * reward_risk
                            pat_col[di] = pat
                            break

            if xh and not ah and bh and not ch and dh:
                if _check_bearish_xabcd(xp, ap, bp, cp, dp, pat):
                    entry = close[di]
                    if abs(entry - dp) <= atr[di] * d_tolerance_atr:
                        stop = dp + atr[di] * 0.5
                        risk = stop - entry
                        if risk > 0:
                            side_col[di] = "short"
                            stop_col[di] = stop
                            target_col[di] = entry - risk * reward_risk
                            pat_col[di] = pat
                            break

    out["harmonic_side"] = side_col
    out["harmonic_stop"] = stop_col
    out["harmonic_target"] = target_col
    out["harmonic_pattern"] = pat_col
    return out
