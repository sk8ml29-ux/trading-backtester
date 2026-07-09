"""Single-account portfolio simulation across mixed symbol/strategy pairs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest.costs import CostConfig, cost_config_for, trade_pnl
from backtest.spread import get_spread_monitor
from backtest.engine import BacktestEngine, Trade
from backtest.metrics import compute_metrics
from backtest.mtf import apply_regime_to_entry, load_entry_regime
from backtest.optimized_loader import params_for
from config import BacktestConfig
from strategies import STRATEGIES
from strategies.base import Side

ROOT = Path(__file__).resolve().parent.parent
SQUEEZE_PATH = ROOT / "optimized_squeeze.json"


@dataclass
class PortfolioTrade:
    symbol: str
    strategy: str
    entry_time: datetime
    exit_time: datetime
    side: Side
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    size: float
    pnl: float = 0.0
    costs: float = 0.0
    exit_reason: str = ""
    reason: str = ""


@dataclass
class AccountResult:
    initial_capital: float
    final_equity: float
    trades: list[PortfolioTrade] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    skipped_trades: int = 0
    spread_skipped: int = 0


def _mixed_path(timeframe: str = "30m") -> Path:
    if timeframe in ("30m", ""):
        return ROOT / "mixed_portfolio.json"
    return ROOT / f"mixed_portfolio_{timeframe}.json"


def load_mixed_pairs(strict: bool = True, timeframe: str = "30m") -> list[tuple[str, str]]:
    path = _mixed_path(timeframe)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return [(p["symbol"], p["strategy"]) for p in data["pairs"]]

    scan_path = ROOT / f"universe_scan_{timeframe}.json"
    if not scan_path.exists():
        return []
    data = json.loads(scan_path.read_text(encoding="utf-8"))
    pairs = []
    for sym, info in data.get("best_per_symbol", {}).items():
        ret = float(info.get("total_return_pct", 0))
        pf = info.get("profit_factor", 0)
        wr = float(info.get("win_rate_pct", 0))
        trades = int(info.get("total_trades", 0))
        if ret <= 0 or pf in (0, "0") or float(pf) < 1.0:
            continue
        if strict:
            if trades < 2:
                continue
            if float(pf) < 1.35 and ret < 2.0:
                continue
            if wr < 52 and float(pf) < 1.6:
                continue
        pairs.append((sym, info["strategy"]))
    return pairs


def collect_raw_trades(symbol: str, strategy: str, cfg: BacktestConfig) -> list[Trade]:
    squeeze = {}
    if SQUEEZE_PATH.exists():
        squeeze = json.loads(SQUEEZE_PATH.read_text(encoding="utf-8"))
    extra = squeeze.get(symbol, {}).get("params", {}) if strategy in (
        "squeeze_breakout", "squeeze_bidirectional"
    ) else {}
    params = {**params_for(symbol, strategy), **extra}
    for k, v in params.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    if strategy in ("rsi_mean_reversion", "rsi_bidirectional") and cfg.adx_trend_threshold == 0:
        cfg.adx_trend_threshold = 25.0

    entry_tf = cfg.entry_timeframe or cfg.timeframe or "30m"
    entry_df, regime_df, _ = load_entry_regime(cfg.symbol, entry_tf)
    df = apply_regime_to_entry(entry_df, regime_df, cfg)
    # Use large capital for signal extraction; sizing done in account sim
    cfg.initial_capital = 100_000.0
    result = BacktestEngine(cfg).run(df, STRATEGIES[strategy](cfg))
    return result.trades


def simulate_account(
    pairs: list[tuple[str, str]],
    initial_capital: float = 20_000.0,
    risk_per_trade: float = 0.0075,
    max_concurrent: int = 6,
    costs: CostConfig | None = None,
    entry_tf: str = "30m",
    strategy_overrides: dict | None = None,
) -> AccountResult:
    costs = costs or CostConfig()
    raw: list[tuple[str, str, Trade]] = []
    overrides = strategy_overrides or {}

    base_cfg = BacktestConfig(
        initial_capital=initial_capital,
        risk_per_trade=risk_per_trade,
        commission_pct=costs.commission_pct,
    )

    for symbol, strategy in pairs:
        cfg = BacktestConfig(
            symbol=symbol,
            timeframe=entry_tf,
            entry_timeframe=entry_tf,
            commission_pct=costs.commission_pct,
        )
        for k, v in overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        try:
            for t in collect_raw_trades(symbol, strategy, cfg):
                if t.exit_time is None or t.exit_price is None:
                    continue
                raw.append((symbol, strategy, t))
        except Exception:
            continue

    raw.sort(key=lambda x: x[2].entry_time)

    equity = initial_capital
    open_pos: list[PortfolioTrade] = []
    closed: list[PortfolioTrade] = []
    skipped = 0
    spread_skipped = 0
    spread_monitor = get_spread_monitor()
    curve: list[tuple[datetime, float]] = []

    for symbol, strategy, t in raw:
        ts = t.entry_time
        open_pos = [p for p in open_pos if p.exit_time > ts]
        curve.append((ts, equity))

        if len(open_pos) >= max_concurrent:
            skipped += 1
            continue
        if any(p.symbol == symbol for p in open_pos):
            skipped += 1
            continue

        entry = float(t.entry_price)
        stop = float(t.stop_loss)
        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            skipped += 1
            continue

        check = spread_monitor.check_trade(symbol, entry, stop_dist)
        if not check.tradeable:
            spread_skipped += 1
            continue

        size = (equity * risk_per_trade) / stop_dist
        sym_costs = cost_config_for(symbol, costs)
        pnl, cost = trade_pnl(t.side, entry, float(t.exit_price), size, sym_costs)
        equity += pnl

        closed.append(
            PortfolioTrade(
                symbol=symbol,
                strategy=strategy,
                entry_time=t.entry_time,
                exit_time=t.exit_time,
                side=t.side,
                entry_price=entry,
                exit_price=float(t.exit_price),
                stop_loss=stop,
                take_profit=float(t.take_profit),
                size=size,
                pnl=pnl,
                costs=cost,
                exit_reason=t.exit_reason,
                reason=t.reason,
            )
        )

    if closed:
        curve.append((closed[-1].exit_time, equity))

    return AccountResult(
        initial_capital=initial_capital,
        final_equity=equity,
        trades=closed,
        equity_curve=curve,
        skipped_trades=skipped,
        spread_skipped=spread_skipped,
    )


def account_metrics(result: AccountResult) -> dict:
    if not result.trades:
        return {"total_trades": 0, "final_equity": result.initial_capital}

    pnls = [t.pnl for t in result.trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_costs = sum(t.costs for t in result.trades)
    ret = (result.final_equity / result.initial_capital - 1) * 100

    return {
        "initial_capital": result.initial_capital,
        "final_equity": round(result.final_equity, 2),
        "total_return_pct": round(ret, 2),
        "total_pnl": round(result.final_equity - result.initial_capital, 2),
        "total_trades": len(result.trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        "expectancy": round(sum(pnls) / len(pnls), 2),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses else "inf",
        "total_costs": round(total_costs, 2),
        "skipped_trades": result.skipped_trades,
        "spread_skipped": result.spread_skipped,
        "stop_loss_exits": sum(1 for t in result.trades if t.exit_reason == "stop_loss"),
        "take_profit_exits": sum(1 for t in result.trades if t.exit_reason == "take_profit"),
        "long_trades": sum(1 for t in result.trades if t.side.value == "long"),
        "short_trades": sum(1 for t in result.trades if t.side.value == "short"),
    }
