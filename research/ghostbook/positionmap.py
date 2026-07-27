"""
Position-map reconstruction — the core idea behind Ghost Book.

An exchange publishes how *many* perp contracts are open (open interest), but
never at what prices those positions were opened. That entry-price distribution
is what actually determines who is forced to trade next: a book whose average
entry sits far above spot is a book full of losers, and losers get margin-called.

The reconstruction exploits the fact that open interest is a conserved pool that
only changes through opens and closes. At 5-minute resolution:

    dOI > 0   ->  new contracts were opened, at roughly the current price
    dOI < 0   ->  contracts were closed, which under a no-information
                  assumption removes entries proportionally across the book

Iterating that over years yields `h[p]`, an estimated distribution of the entry
prices of all currently-open contracts. On-chain analytics does this for spot
holders and calls it realised price / MVRV; nobody publishes it for derivatives
open interest, because the flow has to be stitched together from 10^5 daily
dump files. That gap is the edge this module goes after.

From `h` we derive the state variables that describe forced-flow risk:

    cost_basis  notional-weighted average entry price of the open book
    tli         Trapped Leverage Index = price/cost_basis - 1, i.e. the
                aggregate unrealised PnL per unit of notional held by longs
                (shorts see the mirror image)
    frac_uw     share of the open book whose long side is underwater
    disp        log-price dispersion of the book; a tight book means everyone
                gets margin-called at the same time
    fuel_dn/up  share of the book whose liquidation level sits within a short
                distance of spot — the immediately ignitable fuel
    oi_vel      speed of deleveraging, which separates "still loaded" from
                "already flushed"

Everything is causal: state at time t uses only data up to t.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Entry prices are bucketed in log space so the grid is scale-free: one bucket
# is a fixed percentage move regardless of whether the coin trades at 0.001 or
# 100000.
LOG_BUCKET = 0.01          # ~1% per bucket
MAX_GAP_MIN = 90           # a longer hole in the feed invalidates the book
WARMUP_BARS = 288 * 21     # 3 weeks of 5-min bars before the book is trusted


@dataclass(frozen=True)
class MapConfig:
    log_bucket: float = LOG_BUCKET
    checkpoint_min: int = 60          # emit state on this grid
    fuel_leverage: float = 10.0       # assumed leverage of the marginal position
    fuel_band: float = 0.05           # "within 5% of spot" counts as ignitable
    oi_vel_hours: int = 24
    warmup_bars: int = WARMUP_BARS
    close_rule: str = "proportional"  # or "loss_first"


def _summarise(acc: np.ndarray, bucket_price: np.ndarray, price: float,
               cfg: MapConfig) -> tuple[float, float, float, float, float]:
    """Reduce the reconstructed book to its state variables.

    `acc` holds contract counts up to an irrelevant common scale factor, so
    every output here is a ratio and the scale cancels.
    """
    total = acc.sum()
    if total <= 0:
        return (np.nan,) * 5

    cost_basis = float((acc * bucket_price).sum() / total)
    tli = price / cost_basis - 1.0 if cost_basis > 0 else np.nan

    underwater = bucket_price > price
    frac_uw = float(acc[underwater].sum() / total)

    ln_p = np.log(bucket_price)
    mean_ln = float((acc * ln_p).sum() / total)
    var_ln = float((acc * (ln_p - mean_ln) ** 2).sum() / total)
    disp = float(np.sqrt(max(var_ln, 0.0)))

    # A long opened at p is liquidated near p*(1 - 1/L); it is ignitable fuel if
    # that level sits just below spot. Shorts mirror it above spot.
    lev = 1.0 / cfg.fuel_leverage
    lo_long = price / (1.0 - lev) * (1.0 - cfg.fuel_band)
    hi_long = price / (1.0 - lev)
    fuel_dn = float(acc[(bucket_price >= lo_long) & (bucket_price <= hi_long)].sum() / total)

    lo_short = price / (1.0 + lev)
    hi_short = price / (1.0 + lev) * (1.0 + cfg.fuel_band)
    fuel_up = float(acc[(bucket_price >= lo_short) & (bucket_price <= hi_short)].sum() / total)

    return cost_basis, tli, frac_uw, disp, fuel_dn, fuel_up  # type: ignore[return-value]


def reconstruct(metrics: pd.DataFrame, cfg: MapConfig = MapConfig()) -> pd.DataFrame:
    """Rebuild the entry-price distribution of open interest for one symbol.

    `metrics` must carry the 5-minute columns produced by vision_bulk.fetch_metrics:
    time, oi, oi_usd, price, and the crowd positioning ratios.
    """
    if metrics.empty or len(metrics) < cfg.warmup_bars:
        return pd.DataFrame()

    df = metrics.sort_values("time").reset_index(drop=True)
    t = df["time"].to_numpy()
    oi = df["oi"].to_numpy(dtype=np.float64)
    px = df["price"].to_numpy(dtype=np.float64)

    ok = np.isfinite(oi) & np.isfinite(px) & (oi > 0) & (px > 0)
    if ok.sum() < cfg.warmup_bars:
        return pd.DataFrame()
    t, oi, px = t[ok], oi[ok], px[ok]
    df = df.loc[ok].reset_index(drop=True)

    # Fixed log-price grid covering everything this symbol ever traded at.
    ln_px = np.log(px)
    b0 = int(np.floor(ln_px.min() / cfg.log_bucket)) - 2
    b1 = int(np.ceil(ln_px.max() / cfg.log_bucket)) + 2
    nb = b1 - b0 + 1
    bucket_idx = np.clip(np.round(ln_px / cfg.log_bucket).astype(np.int64) - b0, 0, nb - 1)
    bucket_price = np.exp((np.arange(nb) + b0) * cfg.log_bucket)

    gap_min = np.diff(t).astype("timedelta64[m]").astype(np.float64)
    gap_min = np.concatenate([[0.0], gap_min])

    acc = np.zeros(nb, dtype=np.float64)
    scale = 1.0          # true book = scale * acc
    since_reset = 0

    step = max(1, cfg.checkpoint_min // 5)
    out_rows: list[tuple] = []

    for i in range(len(oi)):
        if i == 0 or gap_min[i] > MAX_GAP_MIN or gap_min[i] <= 0:
            acc[:] = 0.0
            scale = 1.0
            since_reset = 0
            acc[bucket_idx[i]] = oi[i]      # seed the book at the observed OI
            continue

        d_oi = oi[i] - oi[i - 1]
        if d_oi > 0:
            # New contracts entered somewhere inside the bar; the midpoint of the
            # two observed marks is the least-biased entry estimate available.
            mid = np.sqrt(px[i] * px[i - 1])
            bi = int(np.clip(round(np.log(mid) / cfg.log_bucket) - b0, 0, nb - 1))
            acc[bi] += d_oi / scale
        elif d_oi < 0 and oi[i - 1] > 0:
            decay = oi[i] / oi[i - 1]
            if cfg.close_rule == "loss_first":
                # Liquidation-flavoured variant: closures bite hardest on the
                # positions furthest underwater rather than uniformly.
                closed = -d_oi / scale
                order = np.argsort(-np.abs(bucket_price - px[i]))
                take = np.minimum(acc[order], np.maximum(closed, 0.0))
                cum = np.cumsum(take)
                cut = np.searchsorted(cum, closed)
                if cut > 0:
                    acc[order[:cut]] -= take[:cut]
                if cut < nb:
                    acc[order[cut]] = max(0.0, acc[order[cut]] - (closed - (cum[cut - 1] if cut else 0.0)))
                acc[acc < 0] = 0.0
            else:
                scale *= decay

        since_reset += 1

        if scale < 1e-6 or scale > 1e6:
            acc *= scale
            scale = 1.0

        if since_reset >= cfg.warmup_bars and i % step == 0:
            cb, tli, frac_uw, disp, fuel_dn, fuel_up = _summarise(
                acc, bucket_price, px[i], cfg)
            out_rows.append((t[i], px[i], oi[i], cb, tli, frac_uw, disp, fuel_dn, fuel_up))

    if not out_rows:
        return pd.DataFrame()

    out = pd.DataFrame(out_rows, columns=["time", "price", "oi", "cost_basis", "tli",
                                          "frac_uw", "disp", "fuel_dn", "fuel_up"])

    # Deleveraging speed and the crowd-positioning columns ride along on the
    # same checkpoint grid.
    per_hour = max(1, cfg.checkpoint_min // 60)
    lag = max(1, cfg.oi_vel_hours // max(per_hour, 1))
    out["oi_vel"] = np.log(out["oi"]).diff(lag)
    out["oi_usd_vel"] = np.log(out["price"] * out["oi"]).diff(lag)

    crowd = df[["time", "acct_ls", "tt_pos_ls", "tt_acct_ls", "taker_ls", "oi_usd"]]
    out = out.merge(crowd, on="time", how="left")
    return out


def reconstruct_universe(metrics_by_symbol: dict[str, pd.DataFrame],
                         cfg: MapConfig = MapConfig(),
                         verbose: bool = True) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for i, (sym, m) in enumerate(metrics_by_symbol.items(), 1):
        try:
            r = reconstruct(m, cfg)
        except Exception as e:                       # a bad symbol must not kill the run
            if verbose:
                print(f"  {sym}: FAIL {repr(e)[:90]}", flush=True)
            continue
        if not r.empty:
            out[sym] = r
        if verbose and i % 25 == 0:
            print(f"  positionmap {i}/{len(metrics_by_symbol)} kept={len(out)}", flush=True)
    return out
