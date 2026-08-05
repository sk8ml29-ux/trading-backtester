"""
Daily signal generation and paper tracking.

Produces the book Ghost Book wants to hold right now, from a short recent slice
of public data rather than the full research archive. Binance Vision publishes
the 5-minute metrics one day in arrears; the lag test in the validation battery
shows a full extra day of staleness costs little, which is exactly why this can
run as a once-a-day batch instead of a latency-sensitive service.

Nothing here places an order. It writes a target book; execution is a separate,
deliberate decision.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .positionmap import reconstruct
from .strategy import SPEC, GhostBookSpec, combine, overhang_scores, target_weights
from .vision_bulk import CACHE, _fetch_zip, _read_csv, _months, METRIC_COLS, BASE, _norm_ms

STATE_DIR = Path(__file__).resolve().parents[2] / "data" / "ghostbook"
STATE_DIR.mkdir(parents=True, exist_ok=True)

_KL_NAMES = ["open_time", "open", "high", "low", "close", "volume", "close_time",
             "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]


def _recent_metrics(symbol: str, days: int, pool: ThreadPoolExecutor) -> pd.DataFrame:
    end = datetime.now(timezone.utc).date()
    dates = [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days, 0, -1)]

    def one(d: str):
        url = f"{BASE}/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{d}.zip"
        z = _fetch_zip(url)
        return None if z is None else _read_csv(z, METRIC_COLS)

    frames = [f for f in pool.map(one, dates) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(df["create_time"], errors="coerce")
    df["oi"] = pd.to_numeric(df["sum_open_interest"], errors="coerce")
    df["oi_usd"] = pd.to_numeric(df["sum_open_interest_value"], errors="coerce")
    for src, dst in {"sum_toptrader_long_short_ratio": "tt_pos_ls",
                     "count_toptrader_long_short_ratio": "tt_acct_ls",
                     "count_long_short_ratio": "acct_ls",
                     "sum_taker_long_short_vol_ratio": "taker_ls"}.items():
        df[dst] = pd.to_numeric(df.get(src), errors="coerce")
    df = df.dropna(subset=["time", "oi", "oi_usd"])
    df = df[df["oi"] > 0]
    df["price"] = df["oi_usd"] / df["oi"]
    return (df[["time", "oi", "oi_usd", "price", "tt_pos_ls", "tt_acct_ls",
                "acct_ls", "taker_ls"]]
            .drop_duplicates("time").sort_values("time").reset_index(drop=True))


def _recent_klines(symbol: str, days: int, pool: ThreadPoolExecutor) -> pd.DataFrame:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days + 40)
    urls = []
    for m in _months(str(start), str(end)):
        urls.append(f"{BASE}/futures/um/monthly/klines/{symbol}/1h/{symbol}-1h-{m}.zip")
    # The current month is only available as daily files.
    for i in range(45, -1, -1):
        d = (end - timedelta(days=i)).strftime("%Y-%m-%d")
        urls.append(f"{BASE}/futures/um/daily/klines/{symbol}/1h/{symbol}-1h-{d}.zip")

    def one(u: str):
        z = _fetch_zip(u)
        return None if z is None else _read_csv(z, _KL_NAMES)

    frames = [f for f in pool.map(one, urls) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(_norm_ms(df["open_time"]), unit="ms")
    for c in ["open", "high", "low", "close", "quote_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return (df[["time", "open", "high", "low", "close", "quote_volume"]]
            .dropna().drop_duplicates("time").sort_values("time").reset_index(drop=True))


def candidate_pool() -> list[str]:
    """Symbols worth evaluating, from the cached liquidity summary."""
    path = CACHE / "liquidity_summary.parquet"
    if path.exists():
        s = pd.read_parquet(path)
        s = s[(s["peak_liq"] >= 20e6) & (s["days_above_20m"] >= 60)]
        return s.sort_values("peak_liq", ascending=False)["symbol"].tolist()
    from .vision_bulk import usdt_perp_symbols
    return usdt_perp_symbols()


def build_book(spec: GhostBookSpec = SPEC, capital_usd: float = 100_000.0,
               workers: int = 48, max_candidates: int = 240,
               verbose: bool = True) -> dict:
    """Compute the target book for right now."""
    syms = candidate_pool()[:max_candidates]
    days = spec.warmup_days
    rows: list[dict] = []

    with ThreadPoolExecutor(workers) as pool:
        for i, sym in enumerate(syms, 1):
            try:
                kl = _recent_klines(sym, days, pool)
                if kl.empty or len(kl) < 24 * 30:
                    continue
                close = kl.set_index("time")["close"].astype(float)
                daily_liq = (kl.set_index("time")["quote_volume"]
                             .rolling(24, min_periods=12).sum()
                             .rolling(24 * 30, min_periods=24 * 5).median())
                liq = float(daily_liq.iloc[-1]) if len(daily_liq) else np.nan
                if not np.isfinite(liq) or liq < spec.min_liq_usd:
                    continue

                m = _recent_metrics(sym, days, pool)
                if m.empty:
                    continue
                book = reconstruct(m, spec.map_cfg)
                if book.empty:
                    continue

                sc = overhang_scores(book, close, spec.lookbacks).iloc[-1]
                lr = np.log(close).diff()
                vol = float(lr.rolling(24 * 14, min_periods=24 * 5).std().iloc[-1])
                rows.append(dict(symbol=sym, liq_usd=liq, vol=vol,
                                 price=float(close.iloc[-1]),
                                 asof=str(sc["time"]),
                                 **{f"score_{n}": float(sc.get(f"score_{n}", np.nan))
                                    for n in spec.lookbacks}))
            except Exception as e:
                if verbose:
                    print(f"  {sym}: skipped ({repr(e)[:70]})", flush=True)
            if verbose and i % 25 == 0:
                print(f"  scanned {i}/{len(syms)} usable={len(rows)}", flush=True)

    if not rows:
        return dict(ok=False, reason="no symbols produced a signal")

    df = pd.DataFrame(rows).set_index("symbol")
    df = df.sort_values("liq_usd", ascending=False).head(spec.max_names)

    signal = combine(df, spec.lookbacks)
    w = target_weights(signal, df["vol"], spec)
    if w.empty:
        return dict(ok=False, reason="not enough names for a book")

    out = df.loc[w.index].copy()
    out["signal"] = signal.reindex(w.index)
    out["weight"] = w
    out["notional_usd"] = w * capital_usd
    out = out.sort_values("weight", ascending=False)

    return dict(ok=True,
                generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                capital_usd=capital_usd, n_names=int(len(out)),
                gross=float(out["weight"].abs().sum()),
                net=float(out["weight"].sum()),
                spec=spec.describe(),
                book=out.reset_index().to_dict("records"))


def save_book(book: dict, path: Path | None = None) -> Path:
    path = path or STATE_DIR / "target_book.json"
    with open(path, "w") as f:
        json.dump(book, f, indent=2, default=str)
    return path


def print_book(book: dict, top: int = 15) -> None:
    if not book.get("ok"):
        print(f"no book: {book.get('reason')}")
        return
    df = pd.DataFrame(book["book"])
    print(f"\nGhost Book target  {book['generated_at']}  "
          f"capital=${book['capital_usd']:,.0f}  names={book['n_names']}  "
          f"gross={book['gross']:.2f}  net={book['net']:+.3f}")
    cols = ["symbol", "signal", "weight", "notional_usd", "price", "liq_usd"]
    print("\nLONGS")
    print(df.head(top)[cols].to_string(index=False, float_format=lambda v: f"{v:,.4f}"))
    print("\nSHORTS")
    print(df.tail(top)[cols].iloc[::-1].to_string(index=False,
                                                  float_format=lambda v: f"{v:,.4f}"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Ghost Book daily signal")
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--candidates", type=int, default=240)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    book = build_book(capital_usd=args.capital, workers=args.workers,
                      max_candidates=args.candidates)
    print_book(book, args.top)
    if book.get("ok"):
        p = save_book(book, Path(args.out) if args.out else None)
        print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
