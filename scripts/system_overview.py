#!/usr/bin/env python3
"""
Systemöversikt — visar HELA trading-systemet på ett ställe.
Läser alla portfölj-filer och sammanställer par, strategier och förväntad profil.
"""
from __future__ import annotations
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PORTFOLIOS = [
    ("mixed_portfolio_oos.json",         "CRYPTO (Binance)",       "--oos --timeframe 15m/1h"),
    ("mixed_portfolio_oos_stocks.json",  "AKTIER/RÅVAROR (trend)", "--stocks"),
    ("mixed_portfolio_oos_meanrev.json", "MEAN-REVERSION (RSI2)",  "--meanrev"),
    ("mixed_portfolio_oos_spicy.json",   "SPICY (conviction)",     "--spicy"),
    ("mixed_portfolio_oos_forex.json",   "FOREX (Dukascopy)",      "--forex"),
]


def fmt(v, suf=""):
    return f"{v}{suf}" if v not in (None, "", 0) else "-"


def main():
    print("=" * 78)
    print("  TRADING-SYSTEM — KOMPLETT ÖVERSIKT")
    print("=" * 78)

    grand_total = 0
    strat_counts: dict[str, int] = {}
    asset_classes = 0

    for fn, label, flag in PORTFOLIOS:
        path = ROOT / fn
        if not path.exists():
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        pairs = [p for p in d.get("pairs", []) if not p.get("disabled")]
        if not pairs:
            continue
        asset_classes += 1
        grand_total += len(pairs)

        print(f"\n┌─ {label}  ({len(pairs)} par)   kör: python3 run_live.py {flag}")
        print(f"│  {'Symbol':<10}{'Strategi':<22}{'TF':>4}{'PF':>6}{'Sharpe':>7}{'WR%':>5}")
        print(f"│  {'-'*54}")
        for p in pairs:
            strat = p["strategy"]
            strat_counts[strat] = strat_counts.get(strat, 0) + 1
            pf = p.get("oos_pf", "-")
            sh = p.get("oos_sharpe", "-")
            wr = p.get("oos_winrate", "-")
            pf_s = f"{pf:.2f}" if isinstance(pf,(int,float)) else "-"
            sh_s = f"{sh:.2f}" if isinstance(sh,(int,float)) else "-"
            wr_s = f"{wr:.0f}" if isinstance(wr,(int,float)) else "-"
            print(f"│  {p['symbol']:<10}{strat:<22}{p.get('timeframe','?'):>4}{pf_s:>6}{sh_s:>7}{wr_s:>5}")

    print("\n" + "=" * 78)
    print(f"  TOTALT: {grand_total} aktiva par över {asset_classes} tillgångsklasser")
    print(f"  Strategier i bruk: {len(strat_counts)}")
    print("=" * 78)
    print("  Strategifördelning:")
    for s, c in sorted(strat_counts.items(), key=lambda x: -x[1]):
        print(f"    {s:<26} {c} par")

    print("\n  Skydd:")
    print("    • Gatekeeper: 20% portfölj-DD halt, 5% dagsförlust, 2% risk/trade-tak")
    print("    • Hälsovakt: pausar strategier som degraderar (grön/gul/röd)")
    print("    • Daglig hälsokoll 07:00 UTC + Telegram-notis vid röd flagg")


if __name__ == "__main__":
    main()
