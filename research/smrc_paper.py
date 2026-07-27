"""Zero-risk OKX paper-forward tracker for Settlement Memory Reserve Carry.

This process never authenticates and never submits an order. It marks a
synthetic long-spot/short-perpetual book from public OKX prices and realized
funding. OKX is intentionally a different venue from the Binance backtest:
paper success therefore tests portability rather than repeating the backtest.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from research.paper_forward import (
    LIQUID,
    basis_pnl,
    fetch_all_funding,
    fetch_market_prices,
    realized_pnl,
    turnover_cost,
)
from research.settlement_memory_carry import Params, _survival_reserve

STATE = Path(__file__).resolve().parent.parent / "data" / "paper" / "smrc_state.json"
LOCK = STATE.with_suffix(".lock")
SCHEMA_VERSION = 1
MIN_COVERAGE = 0.70


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def target_book(funding: dict, prices: dict, previous: dict, params: Params) -> dict:
    """Build the current six-slot paper target from public observations."""
    scored = {}
    for coin, frame in funding.items():
        price = prices.get(coin)
        if price is None or len(frame) < params.min_history_steps:
            continue
        rates = frame.set_index("time")["rate"].astype(float)
        expected, forecast = _survival_reserve(rates, params)
        pred = float(forecast[-1])
        basis = float(price["basis"])
        reserve = (
            max(pred, 0.0) * float(expected[-1])
            + params.basis_capture * min(max(basis, 0.0), params.max_basis)
            - params.round_trip_cost
        )
        scored[coin] = {
            "pred": pred,
            "reserve": reserve,
            "basis": basis,
            **price,
        }

    keep = {}
    for coin, old in previous.items():
        row = scored.get(coin)
        if row is None:
            # Missing public data is not a trading signal.
            keep[coin] = dict(old)
            continue
        if row["pred"] > 0 and row["reserve"] > 0 and row["basis"] >= -0.005:
            keep[coin] = {**old, **row}

    capacity = max(params.slots - len(keep), 0)
    candidates = [
        (coin, row)
        for coin, row in scored.items()
        if coin not in keep
        and row["pred"] >= params.min_funding
        and row["reserve"] >= params.min_reserve
        and 0 <= row["basis"] <= params.max_basis
    ]
    candidates.sort(key=lambda item: item[1]["reserve"], reverse=True)
    for coin, row in candidates[:capacity]:
        keep[coin] = row

    weight = params.leverage / params.slots
    return {
        coin: {
            "g": 1,
            "weight": weight,
            "pred": row["pred"],
            "reserve": row["reserve"],
            "perp_price": row.get("perp_price"),
            "spot_price": row.get("spot_price"),
        }
        for coin, row in keep.items()
    }


def load_state() -> dict | None:
    return json.loads(STATE.read_text()) if STATE.exists() else None


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(temporary, STATE)


@contextlib.contextmanager
def state_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def show(state: dict) -> None:
    total_return = (state["equity"] / state["capital"] - 1) * 100
    print(
        f"SMRC PAPER | equity={state['equity']:.2f} "
        f"return={total_return:+.3f}% updates={len(state['history'])}"
    )
    if not state["positions"]:
        print("  no positions: the reserve filter is protecting capital")
    for coin, pos in sorted(
        state["positions"].items(), key=lambda item: -item[1]["reserve"]
    ):
        print(
            f"  {coin:6s} LONG spot / SHORT perp  weight={pos['weight']:.3f} "
            f"reserve={pos['reserve'] * 100:.3f}%"
        )


def update(args: argparse.Namespace) -> None:
    params = Params()
    state = load_state()
    now = _now_ms()
    since = state["last_ts_ms"] if state else None
    funding = fetch_all_funding(LIQUID, since_ms=since)
    prices = fetch_market_prices(list(funding))
    minimum = int(len(LIQUID) * MIN_COVERAGE)
    if len(funding) < minimum or len(prices) < minimum:
        raise SystemExit(
            f"Safety stop: incomplete OKX data "
            f"(funding {len(funding)}, prices {len(prices)}, need {minimum})"
        )

    if state is None:
        if not args.init:
            raise SystemExit("No paper state. Start with --init.")
        book = target_book(funding, prices, {}, params)
        cost, turnover = turnover_cost({}, book)
        equity = args.capital * (1 - cost)
        state = {
            "schema_version": SCHEMA_VERSION,
            "capital": args.capital,
            "equity": equity,
            "start_ts": str(pd.Timestamp(now, unit="ms", tz="UTC")),
            "last_ts": str(pd.Timestamp(now, unit="ms", tz="UTC")),
            "last_ts_ms": now,
            "positions": book,
            "history": [
                {
                    "time": str(pd.Timestamp(now, unit="ms", tz="UTC")),
                    "pnl": -cost,
                    "turnover": turnover,
                    "event": "init",
                }
            ],
        }
        save_state(state)
        show(state)
        return

    held = state["positions"]
    missing = set(held) - set(funding) | (set(held) - set(prices))
    if missing:
        raise SystemExit("Safety stop: missing held instruments: " + ", ".join(sorted(missing)))

    funding_return, _ = realized_pnl(
        held, funding, state["last_ts_ms"], now
    )
    basis_return, _ = basis_pnl(held, prices)
    new_book = target_book(funding, prices, held, params)
    cost, turnover = turnover_cost(held, new_book)
    net = funding_return + basis_return - cost
    state["equity"] *= 1 + net
    state["positions"] = new_book
    state["last_ts"] = str(pd.Timestamp(now, unit="ms", tz="UTC"))
    state["last_ts_ms"] = now
    state["history"].append(
        {
            "time": state["last_ts"],
            "pnl": net,
            "funding": funding_return,
            "basis": basis_return,
            "trading_cost": cost,
            "turnover": turnover,
        }
    )
    save_state(state)
    show(state)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--capital", type=float, default=100_000.0)
    args = parser.parse_args()
    with state_lock():
        if args.show and not args.init and not args.update:
            state = load_state()
            if state is None:
                raise SystemExit("No paper state. Start with --init.")
            show(state)
        elif args.init or args.update:
            update(args)
        else:
            parser.error("choose --init, --update, or --show")


if __name__ == "__main__":
    main()
