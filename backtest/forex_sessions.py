"""Forex session helpers (UTC). London / Asian / NY overlap."""

from __future__ import annotations

import pandas as pd


def add_session_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Asian range (00-07 UTC), London open (08-11), NY overlap (12-16)."""
    out = df.copy()
    ts = pd.to_datetime(out.index)
    out["_hour"] = ts.hour
    out["_date"] = ts.date

    asian = (
        out[out["_hour"] < 8]
        .groupby("_date")
        .agg(asian_high=("high", "max"), asian_low=("low", "min"))
    )
    out = out.join(asian, on="_date")
    out["asian_range"] = out["asian_high"] - out["asian_low"]

    out["london_open"] = (out["_hour"] >= 8) & (out["_hour"] <= 11)
    out["asian_session"] = (out["_hour"] >= 22) | (out["_hour"] < 8)
    out["ny_overlap"] = (out["_hour"] >= 12) & (out["_hour"] <= 16)

    out.drop(columns=["_hour", "_date"], inplace=True, errors="ignore")
    return out
