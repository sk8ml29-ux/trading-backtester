#!/usr/bin/env python3
"""
Portfolio backtest: run one strategy across many symbols, aggregate all trades.
Answers: how many trades total, win rate, expectancy with 1% risk / 1.5 R:R style.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.engine import BacktestEngine, Trade
from backtest.metrics import compute_metrics
from backtest.mtf import apply_regime_to_entry, load_entry_regime
from backtest.optimized_loader import params_for
from backtest.universe import category_for
from config import BacktestConfig
from strategies import STRATEGIES


def run_symbol(
    symbol: str,
    strategy: str,
    extra: dict | None = None,
    entry_tf: str = "30m",
) -> tuple[list[Trade], dict]:
    entry_df, regime_df, cfg = load_entry_regime(symbol, entry_tf)
    params = extra or params_for(symbol, strategy)
    for k, v in params.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    if strategy in ("rsi_mean_reversion", "rsi_bidirectional") and cfg.adx_trend_threshold == 0:
        cfg.adx_trend_threshold = 25.0
    df = apply_regime_to_entry(entry_df, regime_df, cfg)
    result = BacktestEngine(cfg).run(df, STRATEGIES[strategy](cfg))
    m = compute_metrics(result)
    m["category"] = category_for(symbol)
    return result.trades, m


def aggregate(trades: list[Trade], initial_per_bot: float = 10_000.0) -> dict:
    if not trades:
        return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0, "expectancy": 0}

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        "expectancy": round(sum(pnls) / len(trades), 2),
        "total_pnl": round(sum(pnls), 2),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses else "inf",
    }


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", default="active_pulse")
    parser.add_argument("--symbols", nargs="*", default=None)
    args = parser.parse_args()

    symbols = args.symbols or all_symbols()
    strategy = args.strategy
    all_trades: list[Trade] = []
    per_symbol: dict = {}

    print(f"Portfolio: {strategy} on {len(symbols)} symbols\n")
    print(f"{'Symbol':12} {'Cat':10} {'Trades':>6} {'W':>4} {'L':>4} {'WR%':>6} {'Return%':>8}")
    print("-" * 60)

    for symbol in symbols:
        try:
            trades, m = run_symbol(symbol, strategy)
        except Exception as exc:
            print(f"{symbol:12} ERROR {exc}")
            continue
        all_trades.extend(trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        losses = len(trades) - wins
        per_symbol[symbol] = {**m, "wins": wins, "losses": losses}
        print(
            f"{symbol:12} {m.get('category','?'):10} {m['total_trades']:6} {wins:4} {losses:4} "
            f"{m['win_rate_pct']:6.1f} {m['total_return_pct']:8.2f}"
        )

    agg = aggregate(all_trades)
    profitable_syms = sum(1 for m in per_symbol.values() if m.get("total_return_pct", 0) > 0)

    print("\n=== PORTFOLIO TOTAL ===")
    print(f"Symbols traded:     {len(per_symbol)} ({profitable_syms} profitable)")
    print(f"Total trades:       {agg['total_trades']}")
    print(f"Wins / Losses:      {agg['wins']} / {agg['losses']}")
    print(f"Win rate:           {agg['win_rate_pct']}%")
    print(f"Expectancy/trade:   ${agg['expectancy']}")
    print(f"Profit factor:      {agg['profit_factor']}")
    print(f"Sum PnL (parallel): ${agg['total_pnl']}")

    out = Path(__file__).resolve().parent.parent / f"portfolio_{strategy}.json"
    out.write_text(
        json.dumps({"strategy": strategy, "aggregate": agg, "per_symbol": per_symbol}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
