#!/usr/bin/env python3
"""Paper-trading bot (no real broker). Uses same strategies as the backtester."""

from __future__ import annotations

import argparse
import json
import sys
import time

from backtest.optimized_loader import (
    apply_optimized_to_live,
    forex_oos_portfolio_entries,
    forex_oos_portfolio_pairs,
    mixed_portfolio_pairs,
    oos_portfolio_pairs,
    oos_portfolio_entries,
    profitable_universe_pairs,
    triple_portfolio_pairs,
    scalp_portfolio_pairs,
    stocks_oos_portfolio_entries,
)
from config import LiveConfig
from live.runner import LiveRunner
from live.state import append_log
from strategies import STRATEGIES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paper trading bot")
    parser.add_argument(
        "--strategy",
        choices=list(STRATEGIES.keys()),
        default=None,
        help="Strategy (default: auto from symbol when --optimized)",
    )
    parser.add_argument("--symbol", default="GC=F")
    parser.add_argument("--timeframe", default="30m", help="Entry timeframe (default: 30m)")
    parser.add_argument("--regime-tf", default="1d", help="Daily regime filter (default: 1d)")
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--risk", type=float, default=0.01)
    parser.add_argument("--poll", type=int, default=180, help="Seconds between checks")
    parser.add_argument("--once", action="store_true", help="Run one evaluation and exit")
    parser.add_argument("--reset", action="store_true", help="Reset saved state")
    parser.add_argument("--json", action="store_true", help="Print result as JSON")
    parser.add_argument(
        "--optimized",
        action="store_true",
        help="Use optimized 30m params; auto-pick best strategy per symbol",
    )
    parser.add_argument(
        "--portfolio",
        action="store_true",
        help="Run all pairs for this timeframe (continuous loop unless --once)",
    )
    parser.add_argument("--mixed", action="store_true", help="Legacy mixed portfolio")
    parser.add_argument("--triple", action="store_true", help="Triple MTF portfolio")
    parser.add_argument("--scalp", action="store_true", help="VRS scalp portfolio")
    parser.add_argument(
        "--oos",
        action="store_true",
        help="OOS-validated crypto portfolio (recommended for paper-forward)",
    )
    parser.add_argument(
        "--forex",
        action="store_true",
        help="OOS-validated forex portfolio (Dukascopy, mixed_portfolio_oos_forex.json)",
    )
    parser.add_argument(
        "--stocks",
        action="store_true",
        help="OOS-validated stocks/commodities 1d portfolio (mixed_portfolio_oos_stocks.json)",
    )
    parser.add_argument("--strict-trend", action="store_true", help="MACD strict trend filter")
    return parser.parse_args()


def resolve_portfolio_pairs(args: argparse.Namespace) -> tuple[list, str]:
    if args.stocks:
        entries = stocks_oos_portfolio_entries()
        return entries, "OOS stocks/commodities [1d]"
    if args.forex:
        entries = forex_oos_portfolio_entries(args.timeframe)
        return entries, f"OOS forex [{args.timeframe}]"
    if args.oos:
        entries = oos_portfolio_entries(args.timeframe)
        return entries, f"OOS crypto [{args.timeframe}]"
    if args.scalp:
        return scalp_portfolio_pairs(), f"VRS scalp [{args.timeframe}]"
    if args.triple:
        return triple_portfolio_pairs(), f"triple MTF [{args.timeframe}]"
    if args.mixed:
        return mixed_portfolio_pairs(args.timeframe), f"mixed legacy [{args.timeframe}]"
    return profitable_universe_pairs(), "profitable"


def build_config(args: argparse.Namespace) -> LiveConfig:
    config = LiveConfig(
        symbol=args.symbol,
        timeframe=args.timeframe,
        strategy=args.strategy or "macd_pullback",
        initial_capital=args.capital,
        risk_per_trade=args.risk,
        poll_seconds=args.poll,
        regime_timeframe=args.regime_tf,
        macd_strict_trend=args.strict_trend,
    )
    if args.optimized:
        apply_optimized_to_live(config, auto_strategy=args.strategy is None)
    return config


def reset_state(config: LiveConfig) -> None:
    from pathlib import Path

    path = Path(config.state_file)
    if path.exists():
        path.unlink()
        print(f"Reset state: {path}")


def run_portfolio_loop(args: argparse.Namespace) -> int:
    items, label = resolve_portfolio_pairs(args)
    if not items:
        print(f"No pairs for {label}. Check portfolio JSON.", file=sys.stderr)
        return 1

    # Stocks 1d: check once per hour; crypto 15m/30m: more frequent
    if getattr(args, "stocks", False):
        poll = args.poll or 3600
    else:
        poll = args.poll or (120 if args.timeframe == "15m" else 180 if args.timeframe == "30m" else 300)
    runners: list[LiveRunner] = []
    for item in items:
        if isinstance(item, dict):
            symbol = item["symbol"]
            strategy = item["strategy"]
            params = item.get("params") or {}
        else:
            symbol, strategy = item
            params = {}
        # Per-pair timeframe overrides CLI default (important for 1d stocks portfolio)
        pair_tf = (item.get("timeframe") if isinstance(item, dict) else None) or args.timeframe
        pair_regime_tf = (item.get("regime_timeframe") if isinstance(item, dict) else None) or args.regime_tf
        cfg = LiveConfig(
            symbol=symbol,
            timeframe=pair_tf,
            strategy=strategy,
            initial_capital=args.capital,
            risk_per_trade=args.risk,
            poll_seconds=poll,
            regime_timeframe=pair_regime_tf,
        )
        apply_optimized_to_live(cfg, auto_strategy=False, extra_params=params)
        if args.reset:
            reset_state(cfg)
        runners.append(LiveRunner(cfg, optimized=True))

    print(f"Paper portfolio ({label}): {len(items)} pairs, poll {poll}s, capital {args.capital:,.0f} SEK/bot")
    for item in items:
        if isinstance(item, dict):
            print(f"  {item['symbol']} / {item['strategy']}  params={item.get('params', {})}")
        else:
            print(f"  {item[0]} / {item[1]}")
    print("Ctrl+C to stop.\n")

    log_suffix = "forex" if args.forex else args.timeframe
    log_path = __import__("pathlib").Path(f"data/live/vps_bot_{log_suffix}.log")
    append_log(log_path, f"START portfolio {label} pairs={len(items)}")

    while True:
        results = []
        for runner in runners:
            try:
                outcome = runner.evaluate_latest()
                results.append(outcome)
                line = (
                    f"{outcome['bar_time']} {outcome['symbol']}/{outcome['strategy']} "
                    f"equity={outcome['equity']:.2f} trades={outcome['trade_count']} "
                    f"-> {outcome['status']}"
                )
                if not args.json:
                    print(line)
                append_log(log_path, line)
            except Exception as exc:
                err = f"Error {runner.config.symbol}: {exc}"
                print(err)
                append_log(log_path, err)

        if args.once:
            if args.json:
                print(json.dumps(results, indent=2, default=str))
            break
        time.sleep(poll)

    return 0


def main() -> int:
    args = parse_args()

    # --oos / --forex / --stocks are portfolio modes — run directly without --portfolio flag
    if args.portfolio or args.oos or getattr(args, "stocks", False) or args.forex:
        return run_portfolio_loop(args)

    config = build_config(args)

    if args.reset:
        reset_state(config)

    runner = LiveRunner(config, optimized=args.optimized)

    if args.once:
        result = runner.evaluate_latest()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            strat = config.strategy
            if args.optimized and args.strategy is None:
                strat = f"{strat} (auto)"
            print(f"Paper bot: {strat} on {config.symbol} [{config.timeframe}]")
            print(result)
        return 0

    if config.mode != "paper":
        print("Only paper mode is supported.", file=sys.stderr)
        return 1

    strat_label = config.strategy
    if args.optimized and args.strategy is None:
        strat_label = f"{config.strategy} (auto)"
    print(
        f"Paper bot: {strat_label} on {config.symbol} "
        f"[entry={config.timeframe}, regime={config.regime_timeframe}] "
        f"poll every {config.poll_seconds}s"
    )
    print("Ctrl+C to stop.\n")
    runner.run_loop(once=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
