#!/usr/bin/env python3
"""
Strategi-hälsorapport
=====================
Läser alla live-botars state-filer och jämför mot deras benchmark-profiler.
Visar grön/gul/röd hälsostatus per strategi + rekommenderar paus vid degradering.

Kör:  python3 scripts/strategy_health_report.py
      python3 scripts/strategy_health_report.py --json   (maskinläsbar)
"""
from __future__ import annotations
import sys, json, argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from risk.strategy_health import load_benchmarks, assess_from_state_file

LIVE_DIR = ROOT / "data" / "live"

ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴", "unknown": "⚪"}


def find_state_for(key: str, benchmark: dict) -> Path | None:
    """Hitta state-fil för en benchmark-nyckel (symbol_strategy_tf)."""
    import re
    sym = benchmark["symbol"]
    strat = benchmark["strategy"]
    tf = benchmark.get("timeframe", "")
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", sym).strip("_").lower()
    # Prova med och utan timeframe-suffix
    candidates = [
        LIVE_DIR / f"{safe}_{strat}_{tf}_state.json",
        LIVE_DIR / f"{safe}_{strat}_state.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    benchmarks = load_benchmarks()
    if not benchmarks:
        print("Inga benchmarks — kör scripts/generate_strategy_benchmarks.py först.")
        return 1

    verdicts = []
    for key, bm in benchmarks.items():
        state_path = find_state_for(key, bm)
        if not state_path:
            continue
        v = assess_from_state_file(state_path, bm)
        if v:
            verdicts.append(v)

    if args.json:
        print(json.dumps([v.__dict__ for v in verdicts], indent=2))
        return 0

    if not verdicts:
        print("Inga aktiva strategier med state-filer hittades ännu.")
        print("(Botarna skapar state-filer efter första utvärderingen.)")
        return 0

    order = {"red": 0, "yellow": 1, "green": 2, "unknown": 3}
    verdicts.sort(key=lambda v: order.get(v.status, 9))

    reds = [v for v in verdicts if v.status == "red"]
    yellows = [v for v in verdicts if v.status == "yellow"]
    greens = [v for v in verdicts if v.status == "green"]

    print("=" * 74)
    print("STRATEGI-HÄLSORAPPORT")
    print("=" * 74)
    print(f"{'':2} {'Symbol':<10}{'Strategi':<22} {'Live%':>7} {'Förv%':>7} {'Undre':>7} {'DD%':>6} {'Mån':>4}")
    print("-" * 74)
    for v in verdicts:
        print(f"{ICON[v.status]} {v.symbol:<10}{v.strategy:<22} "
              f"{v.live_return_pct:>6.1f}% {v.expected_return_pct:>6.1f}% "
              f"{v.lower_band_pct:>6.1f}% {v.live_dd_pct:>5.1f}% {v.months_live:>4.1f}")

    print("-" * 74)
    print(f"🟢 {len(greens)} friska   🟡 {len(yellows)} bevaka   🔴 {len(reds)} degraderade")

    if reds:
        print("\n⚠️  ÅTGÄRD KRÄVS — dessa strategier bör pausas:")
        for v in reds:
            print(f"  🔴 {v.symbol}/{v.strategy}: {v.reason}")
    if yellows:
        print("\n👀 BEVAKA — inom normalvariation men släpar:")
        for v in yellows:
            print(f"  🟡 {v.symbol}/{v.strategy}: {v.reason}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
