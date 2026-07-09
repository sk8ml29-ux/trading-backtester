#!/usr/bin/env python3
"""Print win/loss breakdown for strategy comparison."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics
from backtest.mtf import apply_regime_to_entry
from config import BacktestConfig
from scripts.optimize_30m import load_full
from strategies import STRATEGIES

sq_path = Path(__file__).resolve().parent.parent / "optimized_squeeze.json"
sq = json.loads(sq_path.read_text()) if sq_path.exists() else {}

pairs = [
    ("^NDX", "squeeze_breakout", sq.get("^NDX", {}).get("params", {})),
    ("^NDX", "kinetic_equilibrium", {}),
    ("^GSPC", "squeeze_breakout", sq.get("^GSPC", {}).get("params", {})),
    ("^GSPC", "kinetic_equilibrium", {}),
    ("QQQ", "squeeze_breakout", sq.get("QQQ", {}).get("params", {})),
    ("QQQ", "kinetic_equilibrium", {}),
]

print(f"{'Symbol':12} {'Strategy':24} {'Trades':>6} {'Wins':>5} {'Loss':>5} {'WR%':>6} {'Return%':>8} {'PF':>6}")
print("-" * 88)

for sym, strat, extra in pairs:
    entry, regime = load_full(sym)
    cfg = BacktestConfig(symbol=sym, timeframe="30m", entry_timeframe="30m")
    for k, v in extra.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    df = apply_regime_to_entry(entry, regime, cfg)
    result = BacktestEngine(cfg).run(df, STRATEGIES[strat](cfg))
    m = compute_metrics(result)
    wins = sum(1 for t in result.trades if t.pnl > 0)
    losses = sum(1 for t in result.trades if t.pnl <= 0)
    print(
        f"{sym:12} {strat:24} {m['total_trades']:6} {wins:5} {losses:5} "
        f"{m['win_rate_pct']:6.1f} {m['total_return_pct']:8.2f} {m['profit_factor']:>6}"
    )
