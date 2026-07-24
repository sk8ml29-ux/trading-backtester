"""
paper_forward.py — follow the funding harvest LIVE, with zero risk.

Each time you run it with `--update` it:
  1. pulls the latest funding rates from OKX (reachable even where Binance is
     geo-blocked),
  2. computes today's market-neutral book (which coins to short-perp/long-spot
     or long-perp/short-spot),
  3. credits the funding actually paid since your last run to a *paper* account,
  4. saves everything and prints a running equity curve.

No exchange keys, no real money. This is how you verify — over weeks — that the
backtested edge shows up in real life before risking a krona.

Usage:
  python3 -m research.paper_forward --init --capital 100000 --leverage 5
  python3 -m research.paper_forward --update      # run this every 8h (or daily)
  python3 -m research.paper_forward --show        # just print status
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

from research.okx_data import _get, BASE

STATE = Path(__file__).resolve().parent.parent / "data" / "paper" / "state.json"
LOCK = STATE.with_suffix(".lock")
SCHEMA_VERSION = 2

# Liquid OKX USDT-perps (majors + large caps). Independent of the Vision cache so
# the paper tracker works on a fresh machine right after download.
LIQUID = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "AVAX", "LINK", "DOT",
          "LTC", "BCH", "TRX", "ATOM", "NEAR", "APT", "ARB", "OP", "INJ", "SUI",
          "FIL", "AAVE", "UNI", "ETC", "TIA", "SEI", "RUNE", "LDO", "CRV", "ENA",
          "WLD", "PYTH", "JUP", "STX", "GALA"]

# Champion-style parameters (see research/daytrade_best_params.json)
ENTER = 0.00015      # champion: 8h funding threshold (0.015%)
EXIT = 0.0           # decay threshold to close
LOOKBACK = 24        # 8h bars for the funding forecast (mean)
SLOTS = 8            # min slots for fixed-weight deployment
CASH_YIELD = 0.05    # annual yield on idle capital
LEG_COST = 0.0015    # one-way cost per turnover unit (perp + spot legs)
SHORT_SPOT_BORROW_APR = 0.10  # conservative estimate; exchange-specific live
MIN_DATA_COVERAGE = 0.80


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def okx_funding_recent(
    coin: str, since_ms: int | None = None, max_pages: int = 12
) -> pd.DataFrame:
    """Funding rates, newest-last; paginate far enough to cover ``since_ms``."""
    inst = f"{coin}-USDT-SWAP"
    rows, after = [], ""
    for _ in range(max_pages):
        url = f"{BASE}/api/v5/public/funding-rate-history?instId={inst}&limit=100"
        if after:
            url += f"&after={after}"
        try:
            d = _get(url)
        except Exception:
            break
        data = d.get("data", [])
        if not data:
            break
        rows.extend(data)
        after = data[-1]["fundingTime"]
        if since_ms is not None and int(after) <= since_ms:
            break
        if len(data) < 100:
            break
    if not rows:
        return pd.DataFrame(columns=["time", "rate"])
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["fundingTime"].astype("int64"), unit="ms", utc=True)
    df["rate"] = pd.to_numeric(df.get("realizedRate", df["fundingRate"]), errors="coerce")
    df["rate"] = df["rate"].fillna(pd.to_numeric(df["fundingRate"], errors="coerce"))
    return df[["time", "rate"]].dropna().drop_duplicates("time").sort_values("time")


def fetch_all_funding(coins: list[str], since_ms: int | None = None) -> dict:
    out = {}
    for c in coins:
        f = okx_funding_recent(c, since_ms=since_ms)
        if len(f) >= LOOKBACK:
            out[c] = f
    return out


def fetch_market_prices(coins: list[str]) -> dict:
    """Fetch current OKX spot/perp prices in two public API calls."""
    try:
        swaps = _get(f"{BASE}/api/v5/market/tickers?instType=SWAP").get("data", [])
        spots = _get(f"{BASE}/api/v5/market/tickers?instType=SPOT").get("data", [])
    except Exception:
        return {}
    swap_map = {r.get("instId"): r for r in swaps}
    spot_map = {r.get("instId"): r for r in spots}
    out = {}
    for c in coins:
        sw = swap_map.get(f"{c}-USDT-SWAP", {})
        sp = spot_map.get(f"{c}-USDT", {})
        try:
            perp_price = float(sw["last"])
            spot_price = float(sp["last"])
        except (KeyError, TypeError, ValueError):
            continue
        if perp_price > 0 and spot_price > 0:
            out[c] = {
                "perp_price": perp_price,
                "spot_price": spot_price,
                "basis": perp_price / spot_price - 1.0,
            }
    return out


def target_book(
    funding: dict, prev_positions: dict, leverage: float, prices: dict
):
    """Decide today's positions with simple hysteresis. Returns {coin: {g, weight}}."""
    # per-coin forecast + hysteresis vs previous position
    desired = {}
    all_coins = set(funding) | set(prev_positions)
    for c in all_coins:
        f = funding.get(c)
        price = prices.get(c)
        if f is None or len(f) < LOOKBACK or price is None:
            # A temporary API gap is NOT a trading signal. Preserve the old leg.
            if c in prev_positions:
                desired[c] = dict(prev_positions[c])
            continue
        pred = float(f["rate"].tail(LOOKBACK).mean())
        prev_g = prev_positions.get(c, {}).get("g", 0)
        g = prev_g
        if prev_g == 0:
            # Champion basis filter: enter only when current basis supports side.
            if pred > ENTER and price["basis"] >= 0:
                g = 1
            elif pred < -ENTER and price["basis"] <= 0:
                g = -1
        elif prev_g == 1:
            if pred < EXIT:
                g = 0
        elif prev_g == -1:
            if pred > -EXIT:
                g = 0
        if g != 0:
            desired[c] = dict(g=g, pred=pred, **price)
    n = len(desired)
    denom = max(n, SLOTS)
    w = leverage / denom if denom else 0.0
    return {
        c: dict(
            g=v["g"],
            weight=w,
            pred=v.get("pred", prev_positions.get(c, {}).get("pred", 0.0)),
            perp_price=v.get("perp_price"),
            spot_price=v.get("spot_price"),
        )
        for c, v in desired.items()
    }


def realized_pnl(prev_positions: dict, funding: dict, last_ts_ms: int, now_ms: int):
    """Funding actually paid to the previously-held book since last update."""
    pnl_frac = 0.0
    lo = pd.Timestamp(last_ts_ms, unit="ms", tz="UTC")
    hi = pd.Timestamp(now_ms, unit="ms", tz="UTC")
    detail = []
    for c, pos in prev_positions.items():
        f = funding.get(c)
        if f is None:
            continue
        window = f[(f["time"] > lo) & (f["time"] <= hi)]
        got = float((pos["g"] * window["rate"]).sum()) * pos["weight"]
        pnl_frac += got
        if len(window):
            detail.append((c, len(window), round(got * 100, 4)))
    return pnl_frac, detail


def basis_pnl(prev_positions: dict, prices: dict) -> tuple[float, list]:
    """Real spot-vs-perp convergence/divergence since the previous update."""
    total = 0.0
    detail = []
    for c, pos in prev_positions.items():
        cur = prices.get(c)
        old_perp = pos.get("perp_price")
        old_spot = pos.get("spot_price")
        if cur is None or not old_perp or not old_spot:
            continue
        perp_ret = cur["perp_price"] / old_perp - 1.0
        spot_ret = cur["spot_price"] / old_spot - 1.0
        got = pos["g"] * (spot_ret - perp_ret) * pos["weight"]
        total += got
        detail.append((c, got))
    return total, detail


def short_spot_borrow_cost(prev_positions: dict, elapsed_days: float) -> float:
    """Conservative borrowing cost for g=-1 (long perp / short spot)."""
    short_spot_notional = sum(
        p["weight"] for p in prev_positions.values() if p.get("g") == -1
    )
    return short_spot_notional * SHORT_SPOT_BORROW_APR * elapsed_days / 365.0


def turnover_cost(old_book: dict, new_book: dict) -> tuple[float, float]:
    turnover = 0.0
    for c in set(old_book) | set(new_book):
        old = old_book.get(c, {})
        new = new_book.get(c, {})
        old_signed = old.get("g", 0) * old.get("weight", 0.0)
        new_signed = new.get("g", 0) * new.get("weight", 0.0)
        turnover += abs(new_signed - old_signed)
    return turnover * LEG_COST, turnover


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return None


def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2, default=str))
    os.replace(tmp, STATE)


@contextlib.contextmanager
def state_lock():
    """Prevent cron/manual updates from writing the paper account simultaneously."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield


def fmt_book(positions: dict):
    lines = []
    for c, p in sorted(positions.items(), key=lambda x: -abs(x[1]["pred"])):
        side = "SHORT perp / LÅNG spot" if p["g"] > 0 else "LÅNG perp / SHORT spot"
        lines.append(f"    {c+'USDT':12s} {side:24s} vikt={p['weight']:.3f}  (funding≈{p['pred']*100:.4f}%/8h)")
    return "\n".join(lines) if lines else "    (inga positioner — funding för svag just nu)"


def summary(s):
    hist = s.get("history", [])
    eq = s["equity"]; cap = s["capital"]
    ret = (eq / cap - 1) * 100
    days = (pd.Timestamp(s["last_ts"]) - pd.Timestamp(s["start_ts"])).days or 0
    wins = sum(1 for h in hist if h.get("pnl", 0) > 0)
    tot = sum(1 for h in hist if "pnl" in h)
    wr = (wins / tot * 100) if tot else 0
    print("================================================================")
    print(f"  PAPER-KONTO (låtsaspengar) — hävstång {s['leverage']}x")
    print("================================================================")
    print(f"  Startkapital : {cap:,.0f}")
    print(f"  Nu värt      : {eq:,.0f}   ({ret:+.2f}%)")
    print(f"  Dagar aktiv  : {days}")
    print(f"  Uppdateringar: {tot}   varav plus: {wins}  ({wr:.0f}%)")
    print(f"  Modellversion: {s.get('schema_version', 1)}")
    print(f"  Senast körd  : {s['last_ts']}")
    print("  Aktuell bok:")
    print(fmt_book(s.get("positions", {})))
    if len(hist) >= 2:
        pts = [h["equity"] for h in hist][-40:]
        lo, hi = min(pts), max(pts)
        rng = (hi - lo) or 1
        spark = "".join("▁▂▃▄▅▆▇█"[min(7, int((p - lo) / rng * 7))] for p in pts)
        print(f"  Kurva        : {spark}")
    print("================================================================")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="starta ett nytt paper-konto")
    ap.add_argument("--update", action="store_true", help="hämta funding + uppdatera konto")
    ap.add_argument("--show", action="store_true", help="visa status")
    ap.add_argument("--capital", type=float, default=100000.0)
    ap.add_argument("--leverage", type=float, default=5.0)
    args = ap.parse_args()

    with state_lock():
        _main_locked(args)


def _main_locked(args):
    s = load_state()

    if args.init or s is None:
        now = _now_ms()
        funding = fetch_all_funding(LIQUID)
        min_coins = int(len(LIQUID) * MIN_DATA_COVERAGE)
        if len(funding) < min_coins:
            print(
                f"SÄKERHETSSTOPP: OKX gav bara data för {len(funding)}/{len(LIQUID)} "
                "coins. Inget paper-konto skapades. Försök igen senare."
            )
            return
        prices = fetch_market_prices(list(funding))
        if len(prices) < min_coins:
            print(
                f"SÄKERHETSSTOPP: OKX gav bara priser för {len(prices)}/{len(LIQUID)} "
                "coins. Inget paper-konto skapades. Försök igen senare."
            )
            return
        initial_book = target_book(funding, {}, args.leverage, prices)
        entry_cost, entry_turnover = turnover_cost({}, initial_book)
        initial_equity = args.capital * (1 - entry_cost)
        s = dict(schema_version=SCHEMA_VERSION,
                 capital=args.capital, equity=initial_equity, leverage=args.leverage,
                 start_ts=str(pd.Timestamp(now, unit="ms", tz="UTC")),
                 last_ts=str(pd.Timestamp(now, unit="ms", tz="UTC")),
                 last_ts_ms=now, positions=initial_book,
                 history=[dict(time=str(pd.Timestamp(now, unit="ms", tz="UTC")),
                               equity=round(initial_equity, 2),
                               pnl=round(-entry_cost * 100, 4),
                               event="start", turnover=round(entry_turnover, 4))])
        print(f"Nytt paper-konto: kapital {args.capital:,.0f}, hävstång {args.leverage}x")
        print(f"Startkostnad bokförd: {-entry_cost*100:.4f}%")
        save_state(s)
        summary(s)
        if not args.update:
            print("\nKör '--update' var 8:e timme (eller dagligen) för att följa kontot.")
            return

    if args.show and not args.update:
        summary(s)
        return

    if args.update:
        now = _now_ms()
        funding = fetch_all_funding(LIQUID, since_ms=s["last_ts_ms"])
        if not funding:
            print("Kunde inte hämta funding (nätverk?). Försök igen om en stund.")
            return
        min_coins = int(len(LIQUID) * MIN_DATA_COVERAGE)
        if len(funding) < min_coins:
            print(
                f"SÄKERHETSSTOPP: OKX gav bara data för {len(funding)}/{len(LIQUID)} "
                "coins. Inga positioner ändrades. Försök igen senare."
            )
            return
        held = set(s.get("positions", {}))
        missing_funding = held - set(funding)
        prices = fetch_market_prices(list(set(funding) | held))
        missing_prices = held - set(prices)
        if missing_funding or missing_prices:
            print("SÄKERHETSSTOPP: ofullständig OKX-data. Inga positioner ändrades.")
            if missing_funding:
                print("  Funding saknas för:", ", ".join(sorted(missing_funding)))
            if missing_prices:
                print("  Priser saknas för:", ", ".join(sorted(missing_prices)))
            print("Försök igen om en stund.")
            return

        # Migrate old paper state: seed prices and book the previously omitted entry cost.
        migration_cost = 0.0
        migrated = s.get("schema_version", 1) < SCHEMA_VERSION
        if migrated:
            for c, pos in s.get("positions", {}).items():
                if c in prices:
                    pos.update({
                        "perp_price": prices[c]["perp_price"],
                        "spot_price": prices[c]["spot_price"],
                    })
            migration_cost, _ = turnover_cost({}, s.get("positions", {}))
            s["schema_version"] = SCHEMA_VERSION
            print(
                f"Paper-kontot uppgraderades till modell v{SCHEMA_VERSION}; "
                f"tidigare saknad startkostnad bokförs ({migration_cost*100:.4f}%)."
            )
        # 1) realized funding since last run on the book we were holding
        pnl_frac, detail = realized_pnl(s.get("positions", {}), funding,
                                        s["last_ts_ms"], now)
        # 2) real basis PnL and short-spot borrow cost
        basis_frac, _ = basis_pnl(s.get("positions", {}), prices)
        elapsed_days = max((now - s["last_ts_ms"]) / 86400000.0, 0.0)
        borrow_frac = short_spot_borrow_cost(s.get("positions", {}), elapsed_days)
        # 3) idle-capital cash yield
        deployed = sum(p["weight"] for p in s.get("positions", {}).values()) / max(s["leverage"], 1e-9)
        idle = max(0.0, 1.0 - deployed)
        cash_frac = idle * CASH_YIELD * elapsed_days / 365.0
        # 4) new target book + full signed-weight turnover cost
        # Old v1 positions were opened with a lower threshold and no basis filter.
        # On migration, rebuild from scratch so the account truly follows v2.
        signal_prev = {} if migrated else s.get("positions", {})
        new_book = target_book(funding, signal_prev, s["leverage"], prices)
        cost_frac, turnover = turnover_cost(s.get("positions", {}), new_book)

        day_pnl_frac = (
            pnl_frac + basis_frac + cash_frac
            - borrow_frac - cost_frac - migration_cost
        )
        s["equity"] *= (1 + day_pnl_frac)
        s["positions"] = new_book
        s["last_ts"] = str(pd.Timestamp(now, unit="ms", tz="UTC"))
        s["last_ts_ms"] = now
        s["history"].append(dict(time=s["last_ts"], equity=round(s["equity"], 2),
                                 pnl=round(day_pnl_frac * 100, 4),
                                 funding=round(pnl_frac * 100, 4),
                                 basis=round(basis_frac * 100, 4),
                                 cash=round(cash_frac * 100, 4),
                                 borrow=round(borrow_frac * 100, 4),
                                 trading_cost=round((cost_frac + migration_cost) * 100, 4),
                                 turnover=round(turnover, 4)))
        save_state(s)
        if detail:
            print("Funding mottagen sedan förra körningen (topp):")
            for c, n, bps in sorted(detail, key=lambda x: -abs(x[2]))[:8]:
                print(f"    {c+'USDT':12s} {n} betalningar  -> {bps:+.4f}% av kapitalet")
        print(
            f"\nDenna uppdatering: {day_pnl_frac*100:+.4f}% "
            f"(funding {pnl_frac*100:+.4f}%, basis {basis_frac*100:+.4f}%, "
            f"ränta {cash_frac*100:+.4f}%, spot-lån {-borrow_frac*100:+.4f}%, "
            f"handel {-(cost_frac+migration_cost)*100:+.4f}%)\n"
        )
        summary(s)


if __name__ == "__main__":
    main()
