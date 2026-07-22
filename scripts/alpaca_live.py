#!/usr/bin/env python3
"""
Alpaca live/paper-exekvering — CLI.

Kommandon:
  python3 scripts/alpaca_live.py test            # testa anslutning + visa konto
  python3 scripts/alpaca_live.py run --stocks    # kör stocks-portföljen mot Alpaca
  python3 scripts/alpaca_live.py run --meanrev   # mean-reversion
  python3 scripts/alpaca_live.py run --spicy     # conviction
  python3 scripts/alpaca_live.py positions       # visa öppna positioner

Miljövariabler (skapa gratis konto på alpaca.markets → Paper Trading):
  ALPACA_API_KEY_ID=...
  ALPACA_API_SECRET_KEY=...
  ALPACA_ENV=paper           # 'live' = RIKTIGA PENGAR

Default är PAPER. Live kräver ALPACA_ENV=live explicit.
"""
from __future__ import annotations
import sys, json, time, argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from live.alpaca_broker import AlpacaBroker
from live.alpaca_execution import AlpacaExecutor, build_live_config
import backtest.optimized_loader as _loader


def _entries(which: str) -> list[dict]:
    """Hämta portfölj-entries defensivt (loaders kan saknas på vissa branches)."""
    fn = {
        "stocks": "stocks_oos_portfolio_entries",
        "meanrev": "meanrev_oos_portfolio_entries",
        "spicy": "spicy_oos_portfolio_entries",
    }.get(which)
    loader = getattr(_loader, fn, None)
    if loader is None:
        return []
    return loader()


def cmd_test(broker: AlpacaBroker) -> int:
    print(f"Miljö: {broker.env.upper()}  ({broker.base})")
    if not broker.configured:
        print("❌ Saknar API-nycklar. Sätt ALPACA_API_KEY_ID + ALPACA_API_SECRET_KEY.")
        return 1
    acct = broker.get_account()
    if not acct:
        print("❌ Kunde inte hämta konto — kontrollera nycklar och miljö.")
        return 1
    print("✅ Ansluten till Alpaca!")
    print(f"   Kontostatus:   {acct.get('status')}")
    print(f"   Equity:        ${float(acct.get('equity',0)):,.2f}")
    print(f"   Köpkraft:      ${float(acct.get('buying_power',0)):,.2f}")
    print(f"   Kontanter:     ${float(acct.get('cash',0)):,.2f}")
    print(f"   Marknad öppen: {broker.is_market_open()}")
    return 0


def cmd_positions(broker: AlpacaBroker) -> int:
    if not broker.configured:
        print("❌ Saknar API-nycklar.")
        return 1
    pos = broker.get_positions()
    if not pos:
        print("Inga öppna positioner.")
        return 0
    print(f"{'Symbol':<8}{'Antal':>8}{'Snittpris':>12}{'Nuvarande':>12}{'P/L':>12}")
    for p in pos:
        print(f"{p['symbol']:<8}{float(p['qty']):>8.0f}{float(p['avg_entry_price']):>12.2f}"
              f"{float(p.get('current_price',0)):>12.2f}{float(p.get('unrealized_pl',0)):>12.2f}")
    return 0


def cmd_run(broker: AlpacaBroker, which: str, capital: float, risk: float) -> int:
    if not broker.configured:
        print("❌ Saknar API-nycklar.")
        return 1
    if broker.env == "live":
        print("⚠️  LIVE-LÄGE — RIKTIGA PENGAR. Ctrl+C inom 5s för att avbryta.")
        time.sleep(5)

    entries = _entries(which)
    if not entries:
        print(f"Inga par för '{which}' (portföljfil/loader saknas på denna branch).")
        return 1

    execu = AlpacaExecutor(broker)
    print(f"Kör {which}-portföljen ({len(entries)} par) mot Alpaca [{broker.env}]")
    for e in entries:
        cfg = build_live_config(e["symbol"], e["strategy"], e.get("timeframe", "1d"), capital, risk)
        res = execu.evaluate_and_execute(cfg, e.get("params"))
        icon = {"order":"🟢","hold":"·","skip":"·","blocked":"🔴","error":"⚠️"}.get(res["action"], "·")
        print(f"  {icon} {res['symbol']:<8} {res['action']:<8} {res['message']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["test", "run", "positions"])
    ap.add_argument("--stocks", action="store_true")
    ap.add_argument("--meanrev", action="store_true")
    ap.add_argument("--spicy", action="store_true")
    ap.add_argument("--capital", type=float, default=30000)
    ap.add_argument("--risk", type=float, default=0.0075)
    args = ap.parse_args()

    broker = AlpacaBroker()
    if args.command == "test":
        return cmd_test(broker)
    if args.command == "positions":
        return cmd_positions(broker)
    if args.command == "run":
        which = "stocks" if args.stocks else "meanrev" if args.meanrev else "spicy" if args.spicy else None
        if not which:
            print("Ange --stocks, --meanrev eller --spicy")
            return 1
        return cmd_run(broker, which, args.capital, args.risk)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
