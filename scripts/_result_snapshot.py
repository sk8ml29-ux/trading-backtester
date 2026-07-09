#!/usr/bin/env python3
"""Compile all portfolio backtest results to results_snapshot.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.costs import CostConfig
from backtest.portfolio_account import account_metrics, simulate_account

ROOT = Path(__file__).resolve().parent.parent
CAP = 20_000.0
COSTS = CostConfig()


def sim(pairs: list[tuple[str, str]], tf: str) -> dict:
    m = account_metrics(simulate_account(pairs, CAP, 0.0075, 6, COSTS, entry_tf=tf))
    return {
        "capital_sek": CAP,
        "final_equity": round(m["final_equity"], 2),
        "return_pct": round(m.get("total_return_pct", 0), 2),
        "trades": m.get("total_trades", 0),
        "win_rate_pct": round(m.get("win_rate_pct", 0), 2),
        "profit_factor": m.get("profit_factor", 0),
        "pairs": len(pairs),
    }


def load_pairs(path: str) -> list[tuple[str, str]]:
    d = json.loads((ROOT / path).read_text(encoding="utf-8"))
    return [(p["symbol"], p["strategy"]) for p in d.get("pairs", [])]


def main():
    out: list[dict] = []

    # --- Saved JSON (Yahoo-era) ---
    triple = json.loads((ROOT / "triple_mtf_backtest.json").read_text())
    for tf, bot in triple["bots"].items():
        out.append({
            "name": f"Triple MTF ({tf})",
            "strategy": "triple_tf_confluence",
            "symbols": triple["symbols"],
            "capital_sek": CAP,
            "final_equity": bot["final_equity"],
            "return_pct": bot["total_return_pct"],
            "trades": bot["total_trades"],
            "win_rate_pct": bot["win_rate_pct"],
            "profit_factor": bot.get("profit_factor"),
            "data_source": "Yahoo",
            "data_window": "15m/30m ~60d, 1h ~2y",
            "reliability": "In-sample (hela tillgängligt fönster)",
        })
    out.append({
        "name": "Triple MTF (3×20k totalt)",
        "strategy": "triple_tf_confluence",
        "capital_sek": triple["combined"]["start"],
        "final_equity": triple["combined"]["end"],
        "return_pct": triple["combined"]["return_pct"],
        "data_source": "Yahoo",
        "data_window": "Se ovan",
        "reliability": "In-sample",
    })

    scalp = json.loads((ROOT / "scalp_vrs_backtest.json").read_text())
    sm = scalp["metrics"]
    out.append({
        "name": "VRS Scalp (15m)",
        "strategy": "velocity_rejection",
        "symbols": scalp["symbols"],
        "capital_sek": CAP,
        "final_equity": sm["final_equity"],
        "return_pct": sm["total_return_pct"],
        "trades": sm["total_trades"],
        "win_rate_pct": sm["win_rate_pct"],
        "profit_factor": sm["profit_factor"],
        "data_source": "Yahoo",
        "data_window": "~60 dagar",
        "reliability": "In-sample",
    })

    # Fresh sim: scalp 4 symbols (USO tillagd)
    scalp_pairs = load_pairs("mixed_portfolio_scalp.json")
    s4 = sim(scalp_pairs, "15m")
    out.append({
        "name": "VRS Scalp (15m, 4 symboler)",
        "strategy": "velocity_rejection",
        "symbols": [p[0] for p in scalp_pairs],
        "data_source": "Yahoo",
        "data_window": "~60 dagar",
        "reliability": "In-sample (uppdaterad sim)",
        **s4,
    })

    # --- OOS Binance ---
    research = json.loads((ROOT / "research_results.json").read_text())
    out.append({
        "name": "Research scan (walk-forward)",
        "meta": True,
        "total_runs": research["total_runs"],
        "oos_passed": research["oos_passed"],
        "data_source": "Binance",
        "reliability": "28 strategi×symbol klarade OOS-test (30% hållen ut)",
    })

    oos = json.loads((ROOT / "mixed_portfolio_oos.json").read_text())
    by_tf: dict[str, list] = {}
    for p in oos["pairs"]:
        by_tf.setdefault(p["timeframe"], []).append((p["symbol"], p["strategy"]))

    oos_total_start = 0.0
    oos_total_end = 0.0
    for tf in sorted(by_tf):
        pairs = by_tf[tf]
        r = sim(pairs, tf)
        oos_total_start += CAP
        oos_total_end += r["final_equity"]
        out.append({
            "name": f"OOS Crypto ({tf})",
            "strategy": "blandat (se mixed_portfolio_oos.json)",
            "symbols": list({p[0] for p in pairs}),
            "data_source": "Binance",
            "data_window": "15m: ~2.5y, 1h: ~2y, 30m: begränsat",
            "reliability": "Par valda via OOS walk-forward",
            **r,
        })
    out.append({
        "name": "OOS Crypto (alla TF totalt)",
        "capital_sek": oos_total_start,
        "final_equity": round(oos_total_end, 2),
        "return_pct": round((oos_total_end / oos_total_start - 1) * 100, 2),
        "data_source": "Binance",
        "reliability": "OOS-validerade par; XRP 15m dominant",
    })

    # Top OOS single results
    for i, row in enumerate(research["top_10"][:5], 1):
        out.append({
            "name": f"OOS topp #{i}: {row['symbol']} {row['timeframe']}",
            "strategy": row["strategy"],
            "capital_sek": None,
            "return_pct": row["test_return_pct"],
            "trades": row["test_trades"],
            "win_rate_pct": row["test_win_rate_pct"],
            "profit_factor": row["test_profit_factor"],
            "data_source": "Binance",
            "data_window": f"{row['bars']} bars, split {row['split_date'][:10]}",
            "reliability": "Endast OOS-testperiod (30%)",
            "meta": True,
        })

    # --- Legacy mixed (unified account sim) ---
    for label, f, tf in [
        ("Mixed legacy (30m)", "mixed_portfolio.json", "30m"),
        ("Mixed legacy (15m)", "mixed_portfolio_15m.json", "15m"),
        ("Mixed legacy (1h)", "mixed_portfolio_1h.json", "1h"),
    ]:
        path = ROOT / f
        if not path.exists():
            continue
        pairs = load_pairs(f)
        r = sim(pairs, tf)
        window = "~60d" if tf in ("15m", "30m") else "~2y"
        out.append({
            "name": label,
            "strategy": "blandat (bästa per symbol)",
            "data_source": "Yahoo",
            "data_window": window,
            "reliability": "In-sample; många par kan överanpassa",
            **r,
        })

    path = ROOT / "results_snapshot.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    for row in out:
        if row.get("meta"):
            print(row["name"], row.get("oos_passed", row.get("return_pct", "")))
        elif row.get("final_equity"):
            print(
                row["name"],
                f"{row['final_equity']:,.0f} SEK",
                f"({row.get('return_pct', 0):+.1f}%)",
                f"{row.get('trades', '-')} trades",
            )
    print(f"\nSparat: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
