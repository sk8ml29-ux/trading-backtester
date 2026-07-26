"""
funding_bot.py — OKX DEMO funding-harvest bot (fake money only).

It turns the funding "book" into real orders on OKX **demo trading**:
  - g=+1  -> SHORT perp + LONG spot   (collect positive funding)
  - g=-1  -> LONG perp + SHORT spot    (collect negative funding)

Safety first:
  - Default mode is DRY-RUN: it computes and prints the exact order plan but
    sends nothing.
  - Real demo orders are placed only in `exec` mode, and only against OKX demo
    (fake money) via OKXDemoClient (x-simulated-trading: 1).
  - Hard caps: max legs, max notional per order, abort on missing data.

Modes:
  test    read-only connectivity + account check (safe)
  status  show demo positions + this bot's tracked book (safe)
  dry     compute today's book and print the order plan (safe, DEFAULT)
  exec    actually place the plan on OKX demo (fake money)
  close   flatten everything this bot opened on OKX demo

Usage:
  python3 -m live.funding_bot test
  python3 -m live.funding_bot dry --capital 10000 --leverage 1
  python3 -m live.funding_bot exec --capital 10000 --leverage 1 --yes
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from live.okx_client import OKXDemoClient, OKXError, connectivity_report
from research.paper_forward import (
    LIQUID, fetch_all_funding, fetch_market_prices, target_book,
)

STATE = Path(__file__).resolve().parent.parent / "data" / "live" / "funding_bot_state.json"
LOG = Path(__file__).resolve().parent.parent / "data" / "live" / "funding_bot.log"

# Safety caps
MAX_LEGS = 15                 # never hold more than this many coins
MAX_NOTIONAL_FRAC = 0.40      # per-leg notional cannot exceed 40% of capital


def _log(msg: str):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now(tz=timezone.utc).isoformat()}  {msg}"
    with LOG.open("a") as fh:
        fh.write(line + "\n")
    print(msg)


def _floor_to_lot(size: float, lot: float) -> float:
    if lot <= 0:
        return size
    return math.floor(size / lot) * lot


def _fmt(x: float) -> str:
    return f"{x:.10f}".rstrip("0").rstrip(".")


def load_instruments(client: OKXDemoClient) -> dict:
    """Return {coin: {ctVal, swap_lot, swap_min, spot_lot, spot_min}}."""
    out = {}
    swaps = {r["instId"]: r for r in client.instruments("SWAP")}
    spots = {r["instId"]: r for r in client.instruments("SPOT")}
    for coin in LIQUID:
        sw = swaps.get(f"{coin}-USDT-SWAP")
        sp = spots.get(f"{coin}-USDT")
        if not sw or not sp:
            continue
        out[coin] = dict(
            ctVal=float(sw.get("ctVal", 0) or 0),
            swap_lot=float(sw.get("lotSz", 0) or 0),
            swap_min=float(sw.get("minSz", 0) or 0),
            spot_lot=float(sp.get("lotSz", 0) or 0),
            spot_min=float(sp.get("minSz", 0) or 0),
        )
    return out


def desired_targets(book: dict, prices: dict, instruments: dict,
                    capital: float) -> dict:
    """Pure sizing: book -> per-coin concrete legs. No network.

    Returns {coin: {g, notional, spot_base, spot_side, perp_ct, perp_side}}.
    Coins whose rounded size falls below the exchange minimum are skipped.
    """
    out = {}
    for coin, pos in book.items():
        price = prices.get(coin)
        inst = instruments.get(coin)
        if not price or not inst or inst["ctVal"] <= 0:
            continue
        g = pos["g"]
        notional = pos["weight"] * capital
        spot_base = _floor_to_lot(notional / price["spot_price"], inst["spot_lot"])
        perp_ct = _floor_to_lot(
            notional / (price["perp_price"] * inst["ctVal"]), inst["swap_lot"]
        )
        if spot_base < inst["spot_min"] or perp_ct < inst["swap_min"]:
            continue
        out[coin] = dict(
            g=g, notional=notional,
            spot_base=spot_base, spot_side="buy" if g == 1 else "sell",
            perp_ct=perp_ct, perp_side="sell" if g == 1 else "buy",
        )
    return out


def diff_plan(desired: dict, held: dict) -> list:
    """Pure planner: orders to move from `held` to `desired`.

    Each order: dict(coin, leg, inst_id, side, sz, reduce_only, tgt_ccy, action).
    A side flip is handled as close-then-open.
    """
    plan = []

    def close_legs(coin, pos):
        # close spot: opposite side, same base; close perp: reduceOnly opposite
        spot_close = "sell" if pos["g"] == 1 else "buy"
        perp_close = "buy" if pos["g"] == 1 else "sell"
        plan.append(dict(coin=coin, leg="spot", inst_id=f"{coin}-USDT",
                         side=spot_close, sz=_fmt(pos["spot_base"]),
                         reduce_only=False, tgt_ccy="base_ccy", action="close"))
        plan.append(dict(coin=coin, leg="perp", inst_id=f"{coin}-USDT-SWAP",
                         side=perp_close, sz=_fmt(pos["perp_ct"]),
                         reduce_only=True, tgt_ccy=None, action="close"))

    def open_legs(coin, pos):
        plan.append(dict(coin=coin, leg="spot", inst_id=f"{coin}-USDT",
                         side=pos["spot_side"], sz=_fmt(pos["spot_base"]),
                         reduce_only=False, tgt_ccy="base_ccy", action="open"))
        plan.append(dict(coin=coin, leg="perp", inst_id=f"{coin}-USDT-SWAP",
                         side=pos["perp_side"], sz=_fmt(pos["perp_ct"]),
                         reduce_only=False, tgt_ccy=None, action="open"))

    # close coins no longer wanted or that flipped side
    for coin, pos in held.items():
        if coin not in desired or desired[coin]["g"] != pos["g"]:
            close_legs(coin, pos)
    # open new coins or the new side of a flip
    for coin, pos in desired.items():
        if coin not in held or held[coin]["g"] != pos["g"]:
            open_legs(coin, pos)
    return plan


def reconcile_held(prev_held: dict, desired: dict, failed_coins: set) -> dict:
    """Tracked positions after an exec: only reflect orders that succeeded.

    - a desired coin with no failure -> now held (opened/rebalanced)
    - a coin dropped from desired with no failure -> removed (closed)
    - any coin with a failed order -> keep its previous tracked state
    """
    new_held = dict(prev_held)
    for coin, pos in desired.items():
        if coin not in failed_coins:
            new_held[coin] = pos
    for coin in list(new_held):
        if coin not in desired and coin not in failed_coins:
            new_held.pop(coin, None)
    return new_held


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return dict(held={}, capital=None, leverage=None, history=[])


def save_state(s: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2, default=str))
    tmp.replace(STATE)


def build_book(capital: float, leverage: float,
               enter: float | None = None) -> tuple[dict, dict]:
    funding = fetch_all_funding(LIQUID)
    prices = fetch_market_prices(list(funding))
    book = target_book(funding, {}, leverage, prices, enter=enter)
    return book, prices


def check_caps(desired: dict, capital: float) -> list:
    problems = []
    if len(desired) > MAX_LEGS:
        problems.append(f"För många ben ({len(desired)} > {MAX_LEGS}).")
    for coin, d in desired.items():
        if d["notional"] > capital * MAX_NOTIONAL_FRAC:
            problems.append(
                f"{coin}: notional {d['notional']:.0f} > "
                f"{MAX_NOTIONAL_FRAC*100:.0f}% av kapitalet."
            )
    return problems


def print_plan(plan: list, desired: dict):
    if not plan:
        print("  Inget att göra — boken matchar redan (eller inga signaler).")
        return
    print(f"  Orderplan ({len(plan)} ordrar):")
    for o in plan:
        ro = " reduceOnly" if o["reduce_only"] else ""
        print(f"    [{o['action']:5s}] {o['inst_id']:16s} {o['side']:4s} "
              f"sz={o['sz']}{ro}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["test", "status", "dry", "exec", "close"],
                    default="dry", nargs="?")
    ap.add_argument("--capital", type=float, default=10000.0,
                    help="Demo-kapital (USDT) att dimensionera efter")
    ap.add_argument("--leverage", type=float, default=1.0)
    ap.add_argument("--enter", type=float, default=None,
                    help="ENDAST TEST: sänk funding-tröskeln för att validera "
                         "demo-orderläggning (t.ex. 0.00002). Använd ej live.")
    ap.add_argument("--yes", action="store_true", help="bekräfta exec/close")
    args = ap.parse_args()

    if args.mode == "test":
        print(connectivity_report())
        return

    client = OKXDemoClient()

    if args.mode == "status":
        print(connectivity_report())
        s = load_state()
        held = s.get("held", {})
        print(f"\nBotens spårade bok: {len(held)} coins, "
              f"kapital {s.get('capital')}, hävstång {s.get('leverage')}")
        for c, p in held.items():
            side = "SHORT perp/LÅNG spot" if p["g"] == 1 else "LÅNG perp/SHORT spot"
            print(f"    {c:6s} {side}  spot_base={p['spot_base']} perp_ct={p['perp_ct']}")
        if client.has_credentials():
            try:
                pos = client.positions()
                print(f"\nDemo-börsens öppna perp-positioner: {len(pos)}")
                for p in pos:
                    print(f"    {p.get('instId')}: pos={p.get('pos')} upl={p.get('upl')}")
            except OKXError as e:
                print("Kunde inte läsa demo-positioner:", e)
        return

    # dry / exec / close all need the current book + instruments
    print("Hämtar funding, priser och instrument från OKX ...")
    if args.enter is not None:
        print(f"OBS: testtröskel enter={args.enter} (endast för demo-validering).")
    book, prices = build_book(args.capital, args.leverage, enter=args.enter)
    s = load_state()
    held = s.get("held", {})

    if args.mode == "close":
        desired = {}
    else:
        try:
            instruments = load_instruments(client)
        except Exception as e:
            print("Kunde inte hämta instrument:", repr(e)[:150])
            return
        desired = desired_targets(book, prices, instruments, args.capital)
        caps = check_caps(desired, args.capital)
        if caps:
            print("SÄKERHETSSTOPP — orderplanen bröt en gräns:")
            for c in caps:
                print("   -", c)
            return

    plan = diff_plan(desired, held)
    print(f"\nMål: {len(desired)} coins (av {len(book)} i boken). "
          f"Håller nu: {len(held)}.")
    print_plan(plan, desired)

    if args.mode == "dry":
        print("\n(DRY-RUN: inga ordrar skickades. Kör 'exec --yes' för demo-ordrar.)")
        return

    # exec / close
    if not client.has_credentials():
        print("\nSaknar OKX demo-nycklar. Sätt OKX_API_KEY/SECRET/PASSPHRASE först.")
        return
    if not args.yes:
        print("\nLägg till --yes för att faktiskt skicka ordrarna till OKX DEMO.")
        return
    if not plan:
        return

    _log(f"EXEC {args.mode}: {len(plan)} ordrar, kapital {args.capital}, "
         f"hävstång {args.leverage}")
    sent, failed = 0, 0
    failed_coins: set[str] = set()
    for o in plan:
        td_mode = "cash" if o["leg"] == "spot" else "cross"
        try:
            res = client.place_order(
                inst_id=o["inst_id"], side=o["side"], sz=o["sz"],
                td_mode=td_mode, ord_type="market",
                tgt_ccy=o["tgt_ccy"], reduce_only=o["reduce_only"],
            )
            oid = res[0].get("ordId") if res else "?"
            _log(f"  OK {o['action']} {o['inst_id']} {o['side']} sz={o['sz']} ordId={oid}")
            sent += 1
        except OKXError as e:
            _log(f"  FEL {o['inst_id']} {o['side']} sz={o['sz']}: {e}")
            failed += 1
            failed_coins.add(o["coin"])

    # Update tracked book to reflect what ACTUALLY happened, not the intent.
    new_held = reconcile_held(held, desired, failed_coins)
    s["held"] = new_held
    s["capital"] = args.capital
    s["leverage"] = args.leverage
    s.setdefault("history", []).append(dict(
        time=datetime.now(tz=timezone.utc).isoformat(),
        mode=args.mode, sent=sent, failed=failed, legs=len(new_held)))
    save_state(s)
    if failed:
        print(f"\nKlart: {sent} ordrar OK, {failed} MISSLYCKADES. "
              f"Botens bok uppdaterades bara för det som faktiskt gick igenom. "
              f"Logg: {LOG}")
    else:
        print(f"\nKlart: {sent} ordrar skickade till DEMO, 0 fel. Logg: {LOG}")


if __name__ == "__main__":
    main()
