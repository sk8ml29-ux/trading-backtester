"""
Paper-forward tracker.

Records the target book each day and marks the previous one to market using
prices that only became available afterwards, so the equity curve it builds is
made of genuine forward observations rather than backtest output. This is the
step that has to run, and keep working, before any real money is involved.

    python -m research.ghostbook.paper update      fetch prices, mark, re-target
    python -m research.ghostbook.paper report      show the forward record
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .live import STATE_DIR, _recent_klines, build_book
from .strategy import SPEC

STATE = STATE_DIR / "paper_state.json"


def _load() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return dict(created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                capital_usd=None, equity=1.0, holdings={}, history=[])


def _save(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2, default=str))


def _last_prices(symbols: list[str], workers: int = 48) -> dict[str, float]:
    out: dict[str, float] = {}
    with ThreadPoolExecutor(workers) as pool:
        for s in symbols:
            try:
                kl = _recent_klines(s, 5, pool)
                if not kl.empty:
                    out[s] = float(kl["close"].iloc[-1])
            except Exception:
                continue
    return out


def update(capital_usd: float, cost_bps: float = 8.0, workers: int = 48,
           candidates: int = 240) -> dict:
    state = _load()
    state["capital_usd"] = capital_usd

    holdings = state.get("holdings", {})       # symbol -> {weight, price}
    period_ret = 0.0
    if holdings:
        prices = _last_prices(list(holdings))
        for sym, h in holdings.items():
            p_now = prices.get(sym)
            if p_now and h.get("price"):
                period_ret += h["weight"] * (p_now / h["price"] - 1.0)
                h["mark"] = p_now

    book = build_book(capital_usd=capital_usd, workers=workers,
                      max_candidates=candidates, verbose=False)
    if not book.get("ok"):
        return dict(ok=False, reason=book.get("reason"))

    new = {r["symbol"]: dict(weight=float(r["weight"]), price=float(r["price"]))
           for r in book["book"]}

    turnover = 0.0
    keys = set(new) | set(holdings)
    for k in keys:
        turnover += abs(new.get(k, {}).get("weight", 0.0)
                        - holdings.get(k, {}).get("weight", 0.0))
    cost = turnover * cost_bps / 1e4

    net = period_ret - cost
    state["equity"] = float(state.get("equity", 1.0) * (1.0 + net))
    state["holdings"] = new
    state["history"].append(dict(
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        gross_ret=round(period_ret, 6), turnover=round(turnover, 4),
        cost=round(cost, 6), net_ret=round(net, 6),
        equity=round(state["equity"], 6), n_names=len(new)))
    _save(state)
    return dict(ok=True, net_ret=net, equity=state["equity"],
                turnover=turnover, n_names=len(new))


def report() -> None:
    state = _load()
    h = pd.DataFrame(state.get("history", []))
    if h.empty:
        print("no paper history yet — run `update` on a schedule first")
        return
    h["ts"] = pd.to_datetime(h["ts"])
    r = h["net_ret"]
    days = max((h["ts"].iloc[-1] - h["ts"].iloc[0]).days, 1)
    eq = h["equity"]
    sharpe = r.mean() / r.std(ddof=1) * np.sqrt(365) if len(r) > 2 and r.std(ddof=1) > 0 else np.nan
    print(f"Ghost Book paper-forward  capital=${state.get('capital_usd')}  "
          f"observations={len(h)}  span={days}d")
    print(f"  equity      {eq.iloc[-1]:.4f}  ({(eq.iloc[-1]-1)*100:+.2f}%)")
    print(f"  max drawdown {(eq / eq.cummax() - 1).min()*100:+.2f}%")
    print(f"  Sharpe (ann) {sharpe:.2f}")
    print(f"  mean turnover per update {h['turnover'].mean():.3f}")
    print()
    print(h.tail(15).to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="Ghost Book paper-forward")
    ap.add_argument("mode", choices=["update", "report"])
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--cost-bps", type=float, default=SPEC.taker_bps + SPEC.spread_bps)
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--candidates", type=int, default=240)
    args = ap.parse_args()

    if args.mode == "report":
        report()
        return
    res = update(args.capital, args.cost_bps, args.workers, args.candidates)
    if not res.get("ok"):
        print(f"update failed: {res.get('reason')}")
        return
    print(f"marked and re-targeted: net={res['net_ret']*100:+.3f}%  "
          f"equity={res['equity']:.4f}  turnover={res['turnover']:.3f}  "
          f"names={res['n_names']}")


if __name__ == "__main__":
    main()
