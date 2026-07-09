#!/usr/bin/env python3
"""Run backtests for Trading Rush-inspired strategies."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tabulate import tabulate

from backtest.data_loader import fetch_ohlcv
from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics, trades_to_dataframe
from backtest.mtf import build_mtf_dataset, clamp_start_for_timeframe, apply_regime_to_entry, prepare_entry_frame
from config import BacktestConfig
from backtest.optimized_loader import apply_to_config
from strategies import STRATEGIES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest MACD pullback, Donchian breakout, and RSI mean reversion."
    )
    parser.add_argument(
        "--strategy",
        choices=list(STRATEGIES.keys()) + ["all"],
        default="all",
        help="Strategy to test (default: all)",
    )
    parser.add_argument("--symbol", default="GC=F", help="yfinance symbol (default: GC=F gold)")
    parser.add_argument("--timeframe", default="1d", help="Bar interval (legacy, use --entry-tf)")
    parser.add_argument(
        "--entry-tf",
        default="30m",
        help="Entry timeframe e.g. 30m, 1h (default: 30m)",
    )
    parser.add_argument(
        "--regime-tf",
        default="1d",
        help="Higher timeframe for market regime filter (default: 1d)",
    )
    parser.add_argument("--start", default="2015-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=10_000.0, help="Starting capital")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade (0.01 = 1%%)")
    parser.add_argument("--rr", type=float, default=1.5, help="Reward/risk ratio for trend strategies")
    parser.add_argument(
        "--export-trades",
        type=str,
        default=None,
        help="Export trade log CSV to this path",
    )
    parser.add_argument("--json", action="store_true", help="Print metrics as JSON")
    parser.add_argument(
        "--csv",
        default=None,
        help="Load OHLCV from CSV (columns: datetime,open,high,low,close,volume)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-download market data (ignore cache)",
    )
    parser.add_argument(
        "--strict-trend",
        action="store_true",
        help="MACD: only trade in confirmed trend_up regime",
    )
    parser.add_argument(
        "--optimized",
        action="store_true",
        help="Use parameters from optimized_30m.json",
    )
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> BacktestConfig:
    entry_tf = args.entry_tf or args.timeframe
    return BacktestConfig(
        symbol=args.symbol,
        timeframe=entry_tf,
        entry_timeframe=entry_tf,
        regime_timeframe=args.regime_tf,
        start=args.start,
        end=args.end,
        initial_capital=args.capital,
        risk_per_trade=args.risk,
        reward_risk=args.rr,
        macd_strict_trend=args.strict_trend,
    )


def load_dataset(config: BacktestConfig, args: argparse.Namespace):
    entry_tf = config.entry_timeframe or config.timeframe
    regime_tf = config.regime_timeframe

    if entry_tf == regime_tf:
        start = clamp_start_for_timeframe(config.start, entry_tf)
        df = fetch_ohlcv(
            config.symbol,
            entry_tf,
            start,
            config.end,
            csv_path=args.csv,
            refresh=args.refresh,
        )
        return df, entry_tf, start, None

    entry_start = clamp_start_for_timeframe(config.start, entry_tf)
    regime_start = config.start

    print(f"Multi-TF: entry={entry_tf}, regime={regime_tf}")
    entry_df = fetch_ohlcv(
        config.symbol,
        entry_tf,
        entry_start,
        config.end,
        csv_path=args.csv,
        refresh=args.refresh,
    )
    regime_df = fetch_ohlcv(
        config.symbol,
        regime_tf,
        regime_start,
        config.end,
        refresh=args.refresh,
    )
    entry_frame = prepare_entry_frame(entry_df, config)
    df = apply_regime_to_entry(entry_frame, regime_df, config)
    return df, entry_tf, entry_start, (entry_frame, regime_df)


def main() -> int:
    args = parse_args()
    config = build_config(args)

    print(f"Loading {config.symbol} (entry={config.entry_timeframe or config.timeframe}) from {config.start}...")
    try:
        df, entry_tf, start_used, mtf_parts = load_dataset(config, args)
        config.start = start_used
        config.timeframe = entry_tf
    except Exception as exc:
        print(f"Error loading data: {exc}", file=sys.stderr)
        return 1

    print(f"Loaded {len(df)} bars ({df.index[0].date()} -> {df.index[-1].date()})\n")

    names = list(STRATEGIES.keys()) if args.strategy == "all" else [args.strategy]
    all_metrics = []

    for name in names:
        strategy_cls = STRATEGIES[name]
        cfg = apply_to_config(config, name) if args.optimized else config
        strategy = strategy_cls(cfg)
        run_df = df
        if mtf_parts is not None:
            entry_frame, regime_df = mtf_parts
            run_df = apply_regime_to_entry(entry_frame, regime_df, cfg)
        engine = BacktestEngine(cfg)
        result = engine.run(run_df, strategy)
        metrics = compute_metrics(result)
        all_metrics.append(metrics)

        if args.json:
            continue

        print(f"=== {metrics['strategy']} ===")
        table = [[k, v] for k, v in metrics.items() if k not in {"strategy", "symbol", "timeframe"}]
        print(tabulate(table, headers=["Metric", "Value"], tablefmt="simple"))
        print()

        if args.export_trades and len(names) == 1:
            out = Path(args.export_trades)
            trades_to_dataframe(result).to_csv(out, index=False)
            print(f"Trades exported to {out}")

    if args.json:
        print(json.dumps(all_metrics, indent=2))
    elif len(all_metrics) > 1:
        print("=== Summary (all strategies) ===")
        summary_cols = [
            "strategy",
            "total_trades",
            "win_rate_pct",
            "profit_factor",
            "total_return_pct",
            "max_drawdown_pct",
            "cagr_pct",
        ]
        print(
            tabulate(
                [[m[c] for c in summary_cols] for m in all_metrics],
                headers=summary_cols,
                tablefmt="simple",
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
