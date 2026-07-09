"""Generate sample OHLCV CSV for offline backtests."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def generate_bars(days: int = 2500, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2015-01-01", periods=days)
    price = 1200.0
    rows = []

    regime = 1  # 1 trend up, 0 range, -1 trend down
    regime_len = 0

    for d in dates:
        if regime_len <= 0:
            regime = rng.choice([1, 0, -1], p=[0.45, 0.25, 0.30])
            regime_len = int(rng.integers(40, 120))

        if regime == 1:
            drift = rng.normal(0.0012, 0.008)
        elif regime == -1:
            drift = rng.normal(-0.0010, 0.009)
        else:
            drift = rng.normal(0.0, 0.006)

        open_p = price
        close_p = max(50.0, price * (1 + drift))
        high_p = max(open_p, close_p) * (1 + abs(rng.normal(0, 0.003)))
        low_p = min(open_p, close_p) * (1 - abs(rng.normal(0, 0.003)))
        volume = float(rng.integers(1000, 50000))

        rows.append(
            {
                "datetime": d,
                "open": round(open_p, 2),
                "high": round(high_p, 2),
                "low": round(low_p, 2),
                "close": round(close_p, 2),
                "volume": volume,
            }
        )
        price = close_p
        regime_len -= 1

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=2500)
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent.parent / "data" / "sample_ohlcv.csv"),
    )
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df = generate_bars(args.days)
    df.to_csv(out, index=False)
    print(f"Wrote {len(df)} bars to {out}")


if __name__ == "__main__":
    main()
