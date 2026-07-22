from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from backtest.costs import CostConfig, cost_config_for, trade_pnl
from backtest.indicators import add_regime_columns
from backtest.spread import get_spread_monitor
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime | None
    side: Side
    entry_price: float
    exit_price: float | None
    stop_loss: float
    take_profit: float
    size: float
    pnl: float = 0.0
    pnl_pct: float = 0.0
    reason: str = ""
    exit_reason: str = ""


@dataclass
class BacktestResult:
    strategy: str
    symbol: str
    timeframe: str
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    config: BacktestConfig | None = None


class BacktestEngine:
    def __init__(self, config: BacktestConfig):
        self.config = config

    def run(self, df: pd.DataFrame, strategy: Strategy) -> BacktestResult:
        cfg = self.config
        if "regime" in df.columns and "ema_slow" in df.columns:
            data = df.copy()
        else:
            data = add_regime_columns(
                df,
                ema_fast=cfg.regime_ema_fast,
                ema_slow=cfg.regime_ema_slow,
                adx_period=cfg.adx_period,
                adx_trend_threshold=cfg.adx_trend_threshold,
            )
        data = strategy.prepare(data)

        equity = cfg.initial_capital
        equity_points: list[tuple[datetime, float]] = []
        trades: list[Trade] = []
        open_trade: Trade | None = None
        spread_skipped = 0
        spread_monitor = get_spread_monitor()

        warmup = 220
        for i in range(warmup, len(data)):
            row = data.iloc[i]
            prev = data.iloc[i - 1]
            ts = data.index[i]
            high, low, close = float(row["high"]), float(row["low"]), float(row["close"])
            regime = str(row.get("regime", "range"))

            if open_trade is not None:
                exit_price, exit_reason = self._check_exit(open_trade, high, low, close)
                if exit_price is not None:
                    open_trade.exit_time = ts
                    open_trade.exit_price = exit_price
                    open_trade.exit_reason = exit_reason
                    open_trade.pnl = self._trade_pnl(open_trade)
                    open_trade.pnl_pct = open_trade.pnl / equity
                    equity += open_trade.pnl
                    trades.append(open_trade)
                    open_trade = None

            if open_trade is None and strategy.allows_regime(regime):
                signal = strategy.generate_signal(row, prev)
                if signal is not None:
                    stop_dist = abs(close - signal.stop_loss)
                    if cfg.spread_check_enabled:
                        check = spread_monitor.check_trade(cfg.symbol, close, stop_dist)
                        if not check.tradeable:
                            spread_skipped += 1
                            equity_points.append((ts, equity))
                            continue
                    # Conviction-sizing: signal.risk_mult skalar upp risken.
                    # Hård tak-säkring på 6x för att undvika galna storlekar i backtest.
                    risk_mult = max(0.1, min(float(getattr(signal, "risk_mult", 1.0)), 6.0))
                    effective_risk = cfg.risk_per_trade * risk_mult
                    size = self._position_size(equity, close, signal.stop_loss, effective_risk)
                    if size > 0:
                        open_trade = Trade(
                            entry_time=ts,
                            exit_time=None,
                            side=signal.side,
                            entry_price=close,
                            exit_price=None,
                            stop_loss=signal.stop_loss,
                            take_profit=signal.take_profit,
                            size=size,
                            reason=signal.reason,
                        )

            equity_points.append((ts, equity))

        if open_trade is not None:
            last = data.iloc[-1]
            open_trade.exit_time = data.index[-1]
            open_trade.exit_price = float(last["close"])
            open_trade.exit_reason = "end_of_data"
            open_trade.pnl = self._trade_pnl(open_trade)
            open_trade.pnl_pct = open_trade.pnl / equity
            equity += open_trade.pnl
            trades.append(open_trade)
            equity_points[-1] = (data.index[-1], equity)

        equity_curve = pd.Series(
            [e for _, e in equity_points],
            index=[t for t, _ in equity_points],
            name="equity",
        )

        return BacktestResult(
            strategy=strategy.name,
            symbol=cfg.symbol,
            timeframe=cfg.timeframe,
            trades=trades,
            equity_curve=equity_curve,
            config=cfg,
        )

    def _position_size(
        self, equity: float, entry: float, stop: float, risk_fraction: float
    ) -> float:
        risk_amount = equity * risk_fraction
        stop_distance = abs(entry - stop)
        if stop_distance <= 0:
            return 0.0
        return risk_amount / stop_distance

    def _trade_pnl(self, trade: Trade) -> float:
        assert trade.exit_price is not None
        cfg = self.config
        costs = cost_config_for(cfg.symbol, CostConfig(
            commission_pct=cfg.commission_pct,
            slippage_pct=getattr(cfg, "slippage_pct", 0.0),
            spread_pct=getattr(cfg, "spread_pct", 0.0),
        ))
        pnl, _ = trade_pnl(
            trade.side, trade.entry_price, trade.exit_price, trade.size, costs
        )
        return pnl

    def _check_exit(
        self, trade: Trade, high: float, low: float, close: float
    ) -> tuple[float | None, str]:
        if trade.side == Side.LONG:
            if low <= trade.stop_loss:
                return trade.stop_loss, "stop_loss"
            if high >= trade.take_profit:
                return trade.take_profit, "take_profit"
        else:
            if high >= trade.stop_loss:
                return trade.stop_loss, "stop_loss"
            if low <= trade.take_profit:
                return trade.take_profit, "take_profit"
        return None, ""
