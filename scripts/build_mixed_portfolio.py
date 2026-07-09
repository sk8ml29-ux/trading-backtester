#!/usr/bin/env python3
"""Build curated mixed portfolio — validated on 20k SEK account."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.costs import CostConfig
from backtest.portfolio_account import _mixed_path, account_metrics, simulate_account
from scripts.portfolio_backtest import aggregate, run_symbol

ROOT = Path(__file__).resolve().parent.parent
SQUEEZE = ROOT / "optimized_squeeze.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframe", default="30m", help="Entry timeframe (30m, 15m, 1h)")
    args = parser.parse_args()
    tf = args.timeframe

    scan_path = ROOT / f"universe_scan_{tf}.json"
    if not scan_path.exists() and tf == "30m":
        scan_path = ROOT / "universe_scan_extended.json"
    if not scan_path.exists():
        print(f"Missing {scan_path}. Run: py scripts/scan_multi_tf.py or scan_extended.py")
        return 1

    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    squeeze = json.loads(SQUEEZE.read_text()) if SQUEEZE.exists() else {}
    costs = CostConfig()

    candidates: dict[str, tuple[str, dict]] = {}
    for sym, info in scan.get("best_per_symbol", {}).items():
        ret = float(info.get("total_return_pct", 0))
        pf = float(info.get("profit_factor", 0)) if info.get("profit_factor") not in (0, "0", "inf") else 99
        trades_n = int(info.get("total_trades", 0))
        if ret <= 0 or pf < 1.0 or trades_n < 2:
            continue
        if pf < 1.35 and ret < 2.0:
            continue
        candidates[sym] = (info["strategy"], info)

    pairs_out = []
    all_trades = []
    print(f"Building mixed portfolio for {tf}...\n")

    for sym, (strategy, info) in sorted(candidates.items()):
        extra = squeeze.get(sym, {}).get("params", {}) if strategy in (
            "squeeze_breakout", "squeeze_bidirectional"
        ) else {}
        try:
            trades, m = run_symbol(sym, strategy, extra, entry_tf=tf)
        except Exception as exc:
            print(f"  skip {sym}: {exc}")
            continue

        wr = float(m.get("win_rate_pct", 0))
        ret = float(m.get("total_return_pct", info.get("total_return_pct", 0)))
        pf = m.get("profit_factor", info.get("profit_factor"))

        acct = simulate_account([(sym, strategy)], 20_000, 0.0075, 6, costs, entry_tf=tf)
        am = account_metrics(acct)
        acct_pnl = am.get("total_pnl", 0)
        if acct_pnl <= 0:
            print(f"  - {sym:12} {strategy:22} REJECT account PnL {acct_pnl:+.0f} SEK")
            continue

        pairs_out.append({
            "symbol": sym,
            "strategy": strategy,
            "timeframe": tf,
            "category": info.get("category", m.get("category", "")),
            "backtest_return_pct": ret,
            "profit_factor": pf,
            "win_rate_pct": wr,
            "total_trades": int(m.get("total_trades", 0)),
            "account_pnl_sek": round(acct_pnl, 2),
        })
        all_trades.extend(trades)
        print(
            f"  + {sym:12} {strategy:22} {m.get('total_trades', 0)}t "
            f"WR={wr:.0f}% PF={pf} ret={ret:.1f}% acct={acct_pnl:+.0f} SEK"
        )

    agg = aggregate(all_trades)
    out = {
        "description": f"Curated mixed portfolio ({tf}) — account-validated 20k SEK",
        "timeframe": tf,
        "validation": {"capital_sek": 20000, "risk_per_trade": 0.0075},
        "pairs": pairs_out,
        "aggregate_estimate": agg,
    }
    path = _mixed_path(tf)
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    pairs = [(p["symbol"], p["strategy"]) for p in pairs_out]
    full = simulate_account(pairs, 20_000, 0.0075, 6, costs, entry_tf=tf)
    fm = account_metrics(full)
    print(f"\n{len(pairs_out)} pairs -> {path}")
    print(
        f"20k account: {fm['final_equity']:,.0f} SEK ({fm['total_return_pct']:+.1f}%), "
        f"{fm['total_trades']} trades, WR {fm['win_rate_pct']}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
