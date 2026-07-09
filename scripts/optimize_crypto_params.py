"""
Crypto Parameter Optimization
==============================
Systematisk param-grid-sökning för crypto + SOL/LINK/ADA
på donchian_bidirectional och squeeze_bidirectional.

Kör OOS walk-forward (70/30) för varje kombination.
Sparar bästa params per par → candidates/crypto_optimized.json
Uppdaterar mixed_portfolio_oos.json med förbättrade params om vinnare hittas.

Körning:
  python scripts/optimize_crypto_params.py              # alla par + båda strategier
  python scripts/optimize_crypto_params.py --symbol XRP-USD
  python scripts/optimize_crypto_params.py --dry-run    # kör men uppdaterar ej portfölj
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from backtest.data_loader import fetch_ohlcv
from backtest.metrics import compute_metrics
from config import BacktestConfig
from research.walk_forward import run_walk_forward
from strategies import STRATEGIES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(ROOT / "data" / "live" / "crypto_optimize.log"), mode="a"),
    ],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SYMBOLS & TIMEFRAMES
# ─────────────────────────────────────────────────────────────────────────────

CRYPTO_SYMBOLS = [
    "BTC-USD", "ETH-USD", "XRP-USD",   # core OOS portfolio
    "SOL-USD", "LINK-USD", "ADA-USD",  # untested — high potential
]
TIMEFRAMES = ["15m", "30m", "1h"]
START_DATE = "2023-01-01"

# ─────────────────────────────────────────────────────────────────────────────
# PARAMETER GRIDS
# ─────────────────────────────────────────────────────────────────────────────

DONCHIAN_GRID = {
    "donchian_entry":     [16, 20, 24, 32, 40, 48],
    "donchian_exit":      [3, 5, 8],
    "reward_risk":        [1.5, 2.0, 2.5, 3.0, 3.5],
    "adx_trend_threshold": [0, 15, 20, 25],
}

SQUEEZE_GRID = {
    "bb_period":          [15, 20, 25],
    "squeeze_width_pct_max": [0.20, 0.25, 0.30],
    "reward_risk":        [1.5, 2.0, 2.5, 3.0],
    "adx_trend_threshold": [0, 15, 20],
}

STRATEGY_GRIDS = {
    "donchian_bidirectional": DONCHIAN_GRID,
    "squeeze_bidirectional":  SQUEEZE_GRID,
}

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _grid_combinations(grid: dict) -> list[dict]:
    keys = list(grid.keys())
    values = list(grid.values())
    return [dict(zip(keys, combo)) for combo in product(*values)]


def _score(tm: dict) -> float:
    """Same scoring as pipeline._score but operates directly on metrics dict."""
    sharpe  = float(tm.get("sharpe", 0.0))
    sortino = float(tm.get("sortino", 0.0))
    ret     = float(tm.get("total_return_pct", 0))
    trades  = int(tm.get("total_trades", 0))
    pf = tm.get("profit_factor", 0)
    pf_val = 2.5 if pf == "inf" else (float(pf) if pf not in (0, "0", None) else 0.0)
    log_pf = float(np.log(max(pf_val, 0.01)))
    return sharpe * 10 + log_pf * 5 + sortino * 3 + min(trades, 40) * 0.15 + ret * 0.3


def _passes(tm: dict) -> bool:
    trades = int(tm.get("total_trades", 0))
    ret    = float(tm.get("total_return_pct", 0))
    sharpe = float(tm.get("sharpe", 0.0))
    pf = tm.get("profit_factor", 0)
    pf_ok  = pf == "inf" or (pf not in (0, "0", None) and float(pf) >= 1.10)
    return trades >= 15 and ret > 0 and sharpe >= 0.5 and pf_ok


# ─────────────────────────────────────────────────────────────────────────────
# CORE SEARCH
# ─────────────────────────────────────────────────────────────────────────────

def optimize_pair(
    symbol: str,
    timeframe: str,
    strategy_name: str,
) -> dict | None:
    """
    Grid search over STRATEGY_GRIDS[strategy_name].
    Returns best result dict or None if no combination passes OOS.
    """
    grid = STRATEGY_GRIDS[strategy_name]
    combos = _grid_combinations(grid)
    total = len(combos)
    log.info("  %s/%s/%s — testing %d combinations", symbol, strategy_name, timeframe, total)

    try:
        entry_df = fetch_ohlcv(symbol, timeframe, start=START_DATE, refresh=False)
        regime_df = fetch_ohlcv(symbol, "1d", start="2020-01-01", refresh=False)
    except Exception as e:
        log.error("  Data fetch failed: %s", e)
        return None

    if len(entry_df) < 400:
        log.warning("  Too few bars (%d) for %s/%s", len(entry_df), symbol, timeframe)
        return None

    best_score = -999.0
    best_result = None
    passed = 0

    for i, params in enumerate(combos):
        if i % 50 == 0 and i > 0:
            log.info("  Progress: %d/%d tested, %d passed", i, total, passed)

        cfg = BacktestConfig(symbol=symbol, timeframe=timeframe)
        for k, v in params.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)

        strategy_cls = STRATEGIES.get(strategy_name)
        if not strategy_cls:
            continue
        strategy = strategy_cls(cfg)

        try:
            wf = run_walk_forward(
                entry_df, regime_df, cfg, strategy,
                train_ratio=0.7,
                min_test_trades=15,
                min_sharpe=0.5,
                min_profit_factor=1.10,
            )
        except Exception:
            continue

        if not wf.test_pass:
            continue

        passed += 1
        score = _score(wf.test_metrics)
        if score > best_score:
            best_score = score
            tm = wf.test_metrics
            best_result = {
                "symbol":           symbol,
                "strategy":         strategy_name,
                "timeframe":        timeframe,
                "params":           params,
                "score":            round(score, 3),
                "test_return_pct":  float(tm.get("total_return_pct", 0)),
                "test_trades":      int(tm.get("total_trades", 0)),
                "test_profit_factor": tm.get("profit_factor", 0),
                "test_sharpe":      float(tm.get("sharpe", 0)),
                "test_sortino":     float(tm.get("sortino", 0)),
                "test_calmar":      float(tm.get("calmar", 0)),
                "test_max_dd_pct":  float(tm.get("max_drawdown_pct", 0)),
                "test_cagr_pct":    float(tm.get("cagr_pct", 0)),
                "split_date":       wf.split_date,
            }

    log.info("  Done: %d/%d passed — best score %.2f", passed, total, best_score)
    return best_result


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO UPDATE
# ─────────────────────────────────────────────────────────────────────────────

PORTFOLIO_FILE = ROOT / "mixed_portfolio_oos.json"


def _load_portfolio() -> list[dict]:
    if not PORTFOLIO_FILE.exists():
        return []
    return json.loads(PORTFOLIO_FILE.read_text())


def _update_portfolio(winners: list[dict]) -> None:
    """Merge winning params into mixed_portfolio_oos.json.
    Adds new pairs and updates existing ones if the new score is better."""
    portfolio = _load_portfolio()
    existing = {
        (p.get("symbol"), p.get("strategy"), p.get("timeframe")): i
        for i, p in enumerate(portfolio)
    }

    added, updated = 0, 0
    for w in winners:
        key = (w["symbol"], w["strategy"], w["timeframe"])
        entry = {
            "symbol":    w["symbol"],
            "strategy":  w["strategy"],
            "timeframe": w["timeframe"],
            "params":    w["params"],
            "last_optimized": datetime.utcnow().isoformat(),
            "last_oos_metrics": {
                "return_pct":    w["test_return_pct"],
                "trades":        w["test_trades"],
                "profit_factor": w["test_profit_factor"],
                "sharpe":        w["test_sharpe"],
                "sortino":       w["test_sortino"],
                "calmar":        w["test_calmar"],
                "max_dd_pct":    w["test_max_dd_pct"],
                "cagr_pct":      w["test_cagr_pct"],
            },
        }
        if key in existing:
            portfolio[existing[key]].update(entry)
            updated += 1
        else:
            portfolio.append(entry)
            added += 1

    PORTFOLIO_FILE.write_text(json.dumps(portfolio, indent=2))
    log.info("Portfolio updated: %d added, %d updated → %s", added, updated, PORTFOLIO_FILE.name)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Crypto parameter optimization")
    parser.add_argument("--symbol", help="Single symbol (e.g. XRP-USD)")
    parser.add_argument("--timeframe", help="Single timeframe (e.g. 30m)")
    parser.add_argument("--strategy", help="Single strategy name")
    parser.add_argument("--dry-run", action="store_true", help="Run but don't update portfolio")
    args = parser.parse_args()

    symbols    = [args.symbol]    if args.symbol    else CRYPTO_SYMBOLS
    timeframes = [args.timeframe] if args.timeframe else TIMEFRAMES
    strategies = [args.strategy]  if args.strategy  else list(STRATEGY_GRIDS.keys())

    started = datetime.utcnow()
    log.info("=== Crypto optimization started %s ===", started.isoformat())
    log.info("Symbols: %s", symbols)
    log.info("Timeframes: %s", timeframes)
    log.info("Strategies: %s", strategies)

    winners: list[dict] = []

    for symbol in symbols:
        for tf in timeframes:
            for strategy_name in strategies:
                result = optimize_pair(symbol, tf, strategy_name)
                if result:
                    winners.append(result)
                    log.info(
                        "WINNER: %s/%s/%s  Sharpe=%.2f  PF=%s  return=%.1f%%  n=%d",
                        symbol, strategy_name, tf,
                        result["test_sharpe"],
                        result["test_profit_factor"],
                        result["test_return_pct"],
                        result["test_trades"],
                    )

    # Save all winners
    out_path = ROOT / "candidates" / "crypto_optimized.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "generated": datetime.utcnow().isoformat(),
        "total_winners": len(winners),
        "symbols": symbols,
        "timeframes": timeframes,
        "strategies": strategies,
        "winners": sorted(winners, key=lambda x: x["score"], reverse=True),
    }
    out_path.write_text(json.dumps(out, indent=2))
    log.info("Saved %d winners → %s", len(winners), out_path)

    if not args.dry_run and winners:
        _update_portfolio(winners)
    elif args.dry_run:
        log.info("--dry-run: portfolio NOT updated")

    elapsed = (datetime.utcnow() - started).total_seconds()
    log.info("=== Done in %.0fs — %d winners from %d combinations tested ===",
             elapsed, len(winners),
             len(symbols) * len(timeframes) * len(strategies) * max(
                 len(_grid_combinations(DONCHIAN_GRID)),
                 len(_grid_combinations(SQUEEZE_GRID))
             ))

    if winners:
        print("\n── TOP 10 WINNERS ──")
        for w in sorted(winners, key=lambda x: x["score"], reverse=True)[:10]:
            print(
                f"  {w['symbol']:<12} {w['strategy']:<28} {w['timeframe']:<5} "
                f"Sharpe={w['test_sharpe']:.2f}  PF={w['test_profit_factor']}  "
                f"ret={w['test_return_pct']:.1f}%  n={w['test_trades']}"
            )
    else:
        print("\nIngen vinnare hittad — prova bredare grid eller längre dataintervall.")


if __name__ == "__main__":
    main()
