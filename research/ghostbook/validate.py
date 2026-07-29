"""
Validation battery.

A single in-sample/out-of-sample split is easy to get lucky on, so the strategy
has to clear a harder set of hurdles before it is worth running:

  blocks        performance in every consecutive 6-month window, so a result
                driven by one good quarter cannot hide
  concentration share of profit coming from the top few names and the best few
                days; an edge that lives in three prints is not an edge
  null          the signal shuffled across symbols within each timestamp,
                keeping dates, names, turnover and costs identical
  cost          break-even transaction cost
  universe      the same run on liquidity sub-baskets
  lag           results with an extra delay before execution, which is what a
                real operator with imperfect timing would get
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from .backtest import BTConfig, CostModel, run
from .vision_bulk import CACHE

SCORED_PATH = CACHE / "panel_scored.parquet"


def add_ensembles(p: pd.DataFrame) -> pd.DataFrame:
    have = lambda c: c in p.columns              # noqa: E731
    if have("gb_ohv_72") and have("gb_ohv_168"):
        p["gb_ohv_ens2"] = p[["gb_ohv_72", "gb_ohv_168"]].mean(axis=1)
    if have("gb_ohv_72") and have("gb_ohv_168") and have("gb_ohv_336"):
        p["gb_ohv_ens"] = p[["gb_ohv_72", "gb_ohv_168", "gb_ohv_336"]].mean(axis=1)
    if have("gb_oh_72") and have("gb_oh_168"):
        p["gb_oh_ens2"] = p[["gb_oh_72", "gb_oh_168"]].mean(axis=1)
    return p


def blocks(panel: pd.DataFrame, col: str, cfg: BTConfig, months: int = 6) -> pd.DataFrame:
    t0, t1 = panel["time"].min(), panel["time"].max()
    edges = pd.date_range(t0.normalize(), t1 + pd.Timedelta(days=1), freq=f"{months}MS")
    rows = []
    for a, b in zip(edges[:-1], edges[1:]):
        r = run(panel, col, cfg, start=str(a), end=str(b))
        if not r.get("ok") or r["stats"].get("n", 0) < 20:
            continue
        st = r["stats"]
        rows.append(dict(start=str(a.date()), end=str(b.date()), n=st["n"],
                         ret=st["total_return"], sharpe=st["sharpe"],
                         max_dd=st["max_dd"], hit=st["hit"]))
    return pd.DataFrame(rows)


def concentration(panel: pd.DataFrame, col: str, cfg: BTConfig,
                  start: str | None = None) -> dict:
    r = run(panel, col, cfg, start=start)
    if not r.get("ok"):
        return {}
    w, net = r["weights"], r["net"]
    px = panel.pivot_table(index="time", columns="symbol", values="exec_px", aggfunc="last")
    px = px.reindex(w.index)
    ret = (px.shift(-1) / px - 1.0).reindex_like(w).fillna(0.0)
    contrib = (w * ret).reindex(net.index).sum(axis=0).sort_values()
    total = contrib.sum()
    top = contrib.abs().sort_values(ascending=False)
    daily = net.sort_values()
    return dict(
        total_gross=float(total),
        top1_share=float(contrib[top.index[0]] / total) if total else np.nan,
        top5_share=float(contrib[top.index[:5]].sum() / total) if total else np.nan,
        top10_share=float(contrib[top.index[:10]].sum() / total) if total else np.nan,
        n_symbols=int((contrib != 0).sum()),
        best5_days_share=float(daily.tail(5).sum() / net.sum()) if net.sum() else np.nan,
        pnl_without_best5=float((1 + net.drop(daily.tail(5).index)).prod() - 1),
        pnl_all=float((1 + net).prod() - 1),
    )


def null_test(panel: pd.DataFrame, col: str, cfg: BTConfig, start: str | None,
              n_trials: int = 40, seed: int = 11) -> dict:
    real = run(panel, col, cfg, start=start)
    real_sh = real["stats"]["sharpe"] if real.get("ok") else np.nan
    rng = np.random.default_rng(seed)
    p = panel[["time", "symbol", "exec_px", "liq_usd", col]].copy()
    tvals = p["time"].to_numpy()
    vals = p[col].to_numpy()
    sh = []
    for _ in range(n_trials):
        order = np.lexsort((rng.random(len(tvals)), tvals))
        q = p.copy()
        q[col] = vals[order]
        r = run(q, col, cfg, start=start)
        if r.get("ok") and np.isfinite(r["stats"]["sharpe"]):
            sh.append(r["stats"]["sharpe"])
    sh = np.array(sh)
    return dict(real_sharpe=float(real_sh), null_mean=float(sh.mean()),
                null_sd=float(sh.std()), null_max=float(sh.max()),
                p_value=float((sh >= real_sh).mean()), trials=int(len(sh)))


def cost_curve(panel: pd.DataFrame, col: str, cfg: BTConfig, start: str | None,
               levels=(0, 4, 8, 12, 16, 20, 25, 30)) -> pd.DataFrame:
    rows = []
    for bps in levels:
        c = BTConfig(**{**cfg.__dict__, "cost": CostModel(taker_bps=bps, spread_bps=0.0)})
        r = run(panel, col, c, start=start)
        if r.get("ok"):
            rows.append(dict(bps_per_side=bps, cagr=r["stats"]["cagr"],
                             sharpe=r["stats"]["sharpe"]))
    return pd.DataFrame(rows)


def lag_test(panel: pd.DataFrame, col: str, cfg: BTConfig, start: str | None,
             lags_h=(0, 1, 4, 8, 24)) -> pd.DataFrame:
    """Delay the signal further and see how quickly the edge evaporates."""
    rows = []
    for lag in lags_h:
        q = panel.copy()
        if lag:
            q[col] = q.groupby("symbol")[col].shift(lag)
        r = run(q, col, cfg, start=start)
        if r.get("ok"):
            rows.append(dict(extra_lag_h=lag, cagr=r["stats"]["cagr"],
                             sharpe=r["stats"]["sharpe"]))
    return pd.DataFrame(rows)


def universe_split(panel: pd.DataFrame, col: str, cfg: BTConfig,
                   start: str | None) -> pd.DataFrame:
    """Same strategy on the most liquid names versus the rest."""
    p = panel.copy()
    p["_rank"] = p.groupby("time")["liq_usd"].rank(ascending=False, method="first")
    rows = []
    for label, mask in [("top 1-40", p["_rank"] <= 40),
                        ("top 41-120", (p["_rank"] > 40) & (p["_rank"] <= 120)),
                        ("all", p["_rank"] <= 120)]:
        sub = p[mask]
        c = BTConfig(**{**cfg.__dict__, "min_names": 15})
        r = run(sub, col, c, start=start)
        if r.get("ok"):
            rows.append(dict(basket=label, n=r["stats"]["n"], cagr=r["stats"]["cagr"],
                             sharpe=r["stats"]["sharpe"], max_dd=r["stats"]["max_dd"]))
    return pd.DataFrame(rows)


# Crypto perps offered as MiFID-regulated instruments inside the EEA, plus the
# closest Binance equivalents. Kept explicit because whether the strategy works
# on this list decides whether a resident of the EEA can run it at all.
EEA_REGULATED = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
                 "AVAXUSDT", "BCHUSDT", "ZECUSDT", "LINKUSDT", "LTCUSDT", "DOTUSDT",
                 "1000PEPEUSDT", "HYPEUSDT", "BNBUSDT", "TONUSDT", "SUIUSDT",
                 "AAVEUSDT", "UNIUSDT", "NEARUSDT", "TRXUSDT", "FILUSDT"]


def breadth_report(panel: pd.DataFrame, col: str, split: str,
                   sizes=(20, 30, 40, 60, 80, 120)) -> pd.DataFrame:
    """How performance scales with the number of names in the cross-section."""
    p = panel.copy()
    p["_rank"] = p.groupby("time")["liq_usd"].rank(ascending=False, method="first")
    rows = []
    for n in sizes:
        sub = p[p["_rank"] <= n]
        cfg = BTConfig(rebal_h=24, min_names=min(15, n - 2),
                       max_weight=max(0.06, 2.5 / n))
        got = {}
        for tag, (s, e) in {"is": (None, split), "oos": (split, None),
                            "all": (None, None)}.items():
            r = run(sub, col, cfg, start=s, end=e)
            if r.get("ok"):
                got[f"{tag}_sharpe"] = r["stats"]["sharpe"]
                got[f"{tag}_cagr"] = r["stats"]["cagr"]
        if got:
            rows.append(dict(n_names=n, **got))
    return pd.DataFrame(rows)


def tradeable_universe_report(panel: pd.DataFrame, col: str, split: str) -> pd.DataFrame:
    """The strategy on baskets an EEA resident can and cannot actually reach.

    Also splits the liquid cross-section by listing age, which shows the edge is
    genuinely relational: neither the mature half nor the young half reproduces
    what the mixed basket does.
    """
    p = panel.copy()
    p["_rank"] = p.groupby("time")["liq_usd"].rank(ascending=False, method="first")
    first = p.groupby("symbol")["time"].min()
    p["age_d"] = (p["time"] - p["symbol"].map(first)).dt.days

    baskets = {
        "full universe (120)": p["_rank"] <= 120,
        "top 20 by liquidity": p["_rank"] <= 20,
        "EEA-regulated only": p["symbol"].isin(EEA_REGULATED),
        "liquid + mature only": (p["_rank"] <= 60) & (p["age_d"] > 365),
        "liquid + young only": (p["_rank"] <= 60) & (p["age_d"] <= 365),
    }
    rows = []
    for label, mask in baskets.items():
        sub = p[mask]
        cfg = BTConfig(rebal_h=24, min_names=8, max_weight=0.15)
        got = dict(basket=label, avg_names=float(sub.groupby("time").size().mean()))
        for tag, (s, e) in {"is": (None, split), "oos": (split, None),
                            "all": (None, None)}.items():
            r = run(sub, col, cfg, start=s, end=e)
            if r.get("ok"):
                got[f"{tag}_sharpe"] = r["stats"]["sharpe"]
                got[f"{tag}_cagr"] = r["stats"]["cagr"]
        rows.append(got)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--col", default="gb_ohv_ens2")
    ap.add_argument("--rebal", type=int, default=24)
    ap.add_argument("--split", default="2025-04-01")
    ap.add_argument("--long-only", action="store_true")
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    pd.set_option("display.width", 200)
    print("loading scored panel ...", flush=True)
    p = add_ensembles(pd.read_parquet(SCORED_PATH))
    cfg = BTConfig(rebal_h=args.rebal, long_only=args.long_only)
    res: dict = {"col": args.col, "rebal_h": args.rebal}

    print("\n" + "=" * 78)
    print(f"HEADLINE  signal={args.col}  rebal={args.rebal}h  "
          f"cost={cfg.cost.per_side_bps():.0f}bps/side + impact + funding")
    print("=" * 78)
    for tag, (s, e) in {"IS ": (None, args.split), "OOS": (args.split, None),
                        "ALL": (None, None)}.items():
        r = run(p, args.col, cfg, start=s, end=e)
        if not r.get("ok"):
            continue
        st = r["stats"]
        res[tag.strip()] = st
        print(f"  {tag}  yrs={st['years']:.2f}  CAGR={st['cagr']*100:7.2f}%  "
              f"Sharpe={st['sharpe']:5.2f}  Sortino={st['sortino']:5.2f}  "
              f"maxDD={st['max_dd']*100:7.2f}%  Calmar={st['calmar']:5.2f}  "
              f"hit={st['hit']*100:.1f}%")

    print("\n--- consecutive 6-month blocks (whole history) ---")
    b = blocks(p, args.col, cfg)
    print(b.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))
    res["blocks"] = b.to_dict("records")
    if not b.empty:
        print(f"  positive blocks: {(b['ret'] > 0).sum()}/{len(b)}")

    print("\n--- profit concentration (out-of-sample) ---")
    c = concentration(p, args.col, cfg, start=args.split)
    for k, v in c.items():
        print(f"  {k:22s} {v}")
    res["concentration"] = c

    print("\n--- null test: signal shuffled across names (out-of-sample) ---")
    n = null_test(p, args.col, cfg, args.split, n_trials=args.trials)
    print(f"  real Sharpe {n['real_sharpe']:.2f}   null mean {n['null_mean']:.2f} "
          f"sd {n['null_sd']:.2f} max {n['null_max']:.2f}   p={n['p_value']:.3f} "
          f"({n['trials']} shuffles)")
    res["null"] = n

    print("\n--- cost break-even (out-of-sample) ---")
    cc = cost_curve(p, args.col, cfg, args.split)
    print(cc.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))
    res["cost_curve"] = cc.to_dict("records")

    print("\n--- extra execution delay (out-of-sample) ---")
    lt = lag_test(p, args.col, cfg, args.split)
    print(lt.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))
    res["lag"] = lt.to_dict("records")

    print("\n--- liquidity sub-baskets (out-of-sample) ---")
    us = universe_split(p, args.col, cfg, args.split)
    print(us.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))
    res["universe"] = us.to_dict("records")

    print("\n--- breadth: performance vs number of names ---")
    br = breadth_report(p, args.col, args.split)
    print(br.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))
    res["breadth"] = br.to_dict("records")

    print("\n--- baskets an EEA resident can actually reach ---")
    tu = tradeable_universe_report(p, args.col, args.split)
    print(tu.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))
    res["tradeable_universe"] = tu.to_dict("records")

    if args.out:
        with open(args.out, "w") as f:
            json.dump(res, f, indent=2, default=str)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
