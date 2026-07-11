#!/usr/bin/env python3
"""
Scan nya crypto-symboler + utökad strategi-lista mot OOS walk-forward.
Kombinerar med befintliga research_results.json och uppdaterar
mixed_portfolio_oos.json med de bästa validerade paren.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.pipeline import scan_symbol, _score, save_research, build_portfolio_json
from research.walk_forward import run_walk_forward

ROOT = Path(__file__).resolve().parent.parent

# Nya symboler som inte testats innan
NEW_SYMBOLS = [
    "AVAX-USD", "BNB-USD", "MATIC-USD", "LTC-USD",
    "DOT-USD", "ATOM-USD", "NEAR-USD",
]
# Befintliga — kör om med utökad strategi-lista
EXISTING_SYMBOLS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "LINK-USD",
]
ALL_SYMBOLS = EXISTING_SYMBOLS + NEW_SYMBOLS

TIMEFRAMES = ["1h"]  # 1h är snabbare (färre bars) och ger bra OOS-täckning
STRATEGIES = [
    "squeeze_bidirectional",
    "donchian_bidirectional",
    "triple_tf_confluence",
    "macd_pullback",
    "rsi_bidirectional",
    "active_pulse",
    "velocity_rejection",
]
START = "2023-01-01"


def scan_one(sym: str, tf: str) -> list[dict]:
    print(f"  Scanning {sym} {tf}...", flush=True)
    results = scan_symbol(sym, tf, default_start=START, strategies=STRATEGIES)
    passed = sum(1 for r in results if r.get("test_pass"))
    print(f"  {sym} {tf}: {len(results)} runs, {passed} OOS passed", flush=True)
    return results


def main() -> int:
    print("=== CRYPTO OOS SCAN (utökad symbol-lista) ===")
    print(f"Symboler: {ALL_SYMBOLS}")
    print(f"Timeframes: {TIMEFRAMES}")
    print(f"Strategier: {STRATEGIES}")
    print()

    all_rows: list[dict] = []

    # Parallell scan — varje (symbol, tf) i en tråd
    tasks = [(sym, tf) for sym in ALL_SYMBOLS for tf in TIMEFRAMES]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(scan_one, sym, tf): (sym, tf) for sym, tf in tasks}
        for fut in as_completed(futures):
            try:
                all_rows.extend(fut.result())
            except Exception as exc:
                sym, tf = futures[fut]
                print(f"  ERROR {sym} {tf}: {exc}", flush=True)

    valid = [r for r in all_rows if r.get("test_pass") and "error" not in r]
    valid.sort(key=lambda x: x.get("score", -999), reverse=True)

    # best per symbol+tf
    best_per: dict[str, dict] = {}
    for r in valid:
        key = f"{r['symbol']}|{r['timeframe']}"
        if key not in best_per or r["score"] > best_per[key]["score"]:
            best_per[key] = r

    out = {
        "asset_class": "crypto",
        "symbols": ALL_SYMBOLS,
        "timeframes": TIMEFRAMES,
        "strategies": STRATEGIES,
        "default_start": START,
        "total_runs": len(all_rows),
        "oos_passed": len(valid),
        "top_10": valid[:10],
        "best_per_symbol_tf": list(best_per.values()),
        "all_results": all_rows,
    }

    # Spara ny research-fil
    path = save_research(out, ROOT / "research_results_extended.json")
    print(f"\nSparat: {path}")

    # Print summary
    print(f"\nTotal runs: {out['total_runs']}  |  OOS passed: {out['oos_passed']}")
    print("\n=== TOP 20 (OOS score) ===")
    print(f"{'Symbol':<14} {'Strategy':<26} {'TF':<5} {'PF':<6} {'Ret%':<8} {'Trades':<8} {'Score':<7}")
    print("-" * 80)
    for r in valid[:20]:
        pf = r.get("test_profit_factor", 0)
        print(
            f"  {r['symbol']:<12} {r['strategy']:<26} {r['timeframe']:<5} "
            f"{pf:<6.2f} {r['test_return_pct']:<8.1f} {r['test_trades']:<8} {r['score']:.2f}"
        )

    # Uppdatera mixed_portfolio_oos.json
    portfolio = build_portfolio_json(
        out,
        name="oos_extended_crypto",
        description=(
            "Walk-forward OOS crypto (Binance). Utökad scan inkl. AVAX, BNB, "
            "MATIC, LTC, DOT, ATOM, NEAR + alla befintliga par."
        ),
        source="research_results_extended.json",
    )
    oos_path = ROOT / "mixed_portfolio_oos_extended.json"
    oos_path.write_text(json.dumps(portfolio, indent=2), encoding="utf-8")
    print(f"\nPortfolj sparad: {oos_path}")
    print(f"Antal validerade par: {len(portfolio['pairs'])}")

    # Rekommendera vad som ska deployas
    print("\n=== DEPLOY-REKOMMENDATION ===")
    # Filter: min 10 OOS trades, PF >= 1.2 för säkerhet
    deploy_candidates = [
        r for r in valid
        if r["test_trades"] >= 10 and float(r.get("test_profit_factor", 0) or 0) >= 1.2
    ]
    deploy_candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    seen = set()
    for r in deploy_candidates:
        key = f"{r['symbol']}|{r['timeframe']}"
        if key in seen:
            continue
        seen.add(key)
        pf = r.get("test_profit_factor", 0)
        print(
            f"  {r['symbol']:<12} {r['strategy']:<26} {r['timeframe']:<5} "
            f"PF={pf:.2f}  ret={r['test_return_pct']:.1f}%  trades={r['test_trades']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
