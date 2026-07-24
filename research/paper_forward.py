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
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from research.okx_data import _get, BASE

STATE = Path(__file__).resolve().parent.parent / "data" / "paper" / "state.json"

# Liquid OKX USDT-perps (majors + large caps). Independent of the Vision cache so
# the paper tracker works on a fresh machine right after download.
LIQUID = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "AVAX", "LINK", "DOT",
          "LTC", "BCH", "TRX", "ATOM", "NEAR", "APT", "ARB", "OP", "INJ", "SUI",
          "FIL", "AAVE", "UNI", "ETC", "TIA", "SEI", "RUNE", "LDO", "CRV", "ENA",
          "WLD", "PYTH", "JUP", "STX", "GALA"]

# Champion-style parameters (see research/daytrade_best_params.json)
ENTER = 0.00005      # 8h funding threshold to open (0.005%)
EXIT = 0.0           # decay threshold to close
LOOKBACK = 24        # 8h bars for the funding forecast (mean)
SLOTS = 8            # min slots for fixed-weight deployment
CASH_YIELD = 0.05    # annual yield on idle capital
LEG_COST = 0.0015    # round-trip-ish cost per unit turnover (both legs)


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def okx_funding_recent(coin: str, pages: int = 2) -> pd.DataFrame:
    """Recent funding rates (8h) for a coin, newest-last. ~1 page ≈ 33 days."""
    inst = f"{coin}-USDT-SWAP"
    rows, after = [], ""
    for _ in range(pages):
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
        if len(data) < 100:
            break
    if not rows:
        return pd.DataFrame(columns=["time", "rate"])
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["fundingTime"].astype("int64"), unit="ms", utc=True)
    df["rate"] = pd.to_numeric(df.get("realizedRate", df["fundingRate"]), errors="coerce")
    df["rate"] = df["rate"].fillna(pd.to_numeric(df["fundingRate"], errors="coerce"))
    return df[["time", "rate"]].dropna().drop_duplicates("time").sort_values("time")


def fetch_all_funding(coins: list[str]) -> dict:
    out = {}
    for c in coins:
        f = okx_funding_recent(c)
        if len(f) >= LOOKBACK:
            out[c] = f
    return out


def target_book(funding: dict, prev_positions: dict, leverage: float):
    """Decide today's positions with simple hysteresis. Returns {coin: {g, weight}}."""
    # per-coin forecast + hysteresis vs previous position
    desired = {}
    for c, f in funding.items():
        pred = float(f["rate"].tail(LOOKBACK).mean())
        prev_g = prev_positions.get(c, {}).get("g", 0)
        g = prev_g
        if prev_g == 0:
            if pred > ENTER:
                g = 1
            elif pred < -ENTER:
                g = -1
        elif prev_g == 1:
            if pred < EXIT:
                g = 0
        elif prev_g == -1:
            if pred > -EXIT:
                g = 0
        if g != 0:
            desired[c] = dict(g=g, pred=pred)
    n = len(desired)
    denom = max(n, SLOTS)
    w = leverage / denom if denom else 0.0
    return {c: dict(g=v["g"], weight=w, pred=v["pred"]) for c, v in desired.items()}


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


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return None


def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2, default=str))


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

    s = load_state()

    if args.init or s is None:
        now = _now_ms()
        s = dict(capital=args.capital, equity=args.capital, leverage=args.leverage,
                 start_ts=str(pd.Timestamp(now, unit="ms", tz="UTC")),
                 last_ts=str(pd.Timestamp(now, unit="ms", tz="UTC")),
                 last_ts_ms=now, positions={}, history=[])
        print(f"Nytt paper-konto: kapital {args.capital:,.0f}, hävstång {args.leverage}x")
        # set an initial book immediately
        funding = fetch_all_funding(LIQUID)
        s["positions"] = target_book(funding, {}, args.leverage)
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
        funding = fetch_all_funding(LIQUID)
        if not funding:
            print("Kunde inte hämta funding (nätverk?). Försök igen om en stund.")
            return
        # 1) realized funding since last run on the book we were holding
        pnl_frac, detail = realized_pnl(s.get("positions", {}), funding,
                                        s["last_ts_ms"], now)
        # 2) idle-capital cash yield
        deployed = sum(p["weight"] for p in s.get("positions", {}).values()) / max(s["leverage"], 1e-9)
        idle = max(0.0, 1.0 - deployed)
        elapsed_days = max((now - s["last_ts_ms"]) / 86400000.0, 0.0)
        cash_frac = idle * CASH_YIELD * elapsed_days / 365.0
        # 3) new target book + turnover cost
        new_book = target_book(funding, s.get("positions", {}), s["leverage"])
        turnover = 0.0
        keys = set(s.get("positions", {})) | set(new_book)
        for c in keys:
            old = s["positions"].get(c, {}); new = new_book.get(c, {})
            osign = old.get("g", 0) * old.get("weight", 0.0)
            nsign = new.get("g", 0) * new.get("weight", 0.0)
            turnover += abs(nsign - osign)
        cost_frac = turnover * LEG_COST

        day_pnl_frac = pnl_frac + cash_frac - cost_frac
        s["equity"] *= (1 + day_pnl_frac)
        s["positions"] = new_book
        s["last_ts"] = str(pd.Timestamp(now, unit="ms", tz="UTC"))
        s["last_ts_ms"] = now
        s["history"].append(dict(time=s["last_ts"], equity=round(s["equity"], 2),
                                 pnl=round(day_pnl_frac * 100, 4)))
        save_state(s)
        if detail:
            print("Funding mottagen sedan förra körningen (topp):")
            for c, n, bps in sorted(detail, key=lambda x: -abs(x[2]))[:8]:
                print(f"    {c+'USDT':12s} {n} betalningar  -> {bps:+.4f}% av kapitalet")
        print(f"\nDenna uppdatering: {day_pnl_frac*100:+.4f}% (funding {pnl_frac*100:+.4f}%, "
              f"ränta {cash_frac*100:+.4f}%, kostnad {cost_frac*100:-.4f}%)\n")
        summary(s)


if __name__ == "__main__":
    main()
