from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from config import LiveConfig
from live.state import BotState, OpenPosition, append_log
from backtest.spread import get_spread_monitor
from strategies.base import Side, Signal


@dataclass
class FillResult:
    action: str  # open | close | hold | skip
    message: str
    pnl: float = 0.0


class PaperBroker:
    def __init__(self, config: LiveConfig, state: BotState):
        self.config = config
        self.state = state
        self.log_path = __import__("pathlib").Path(config.log_file)

    def on_bar(
        self,
        bar_time: datetime,
        high: float,
        low: float,
        close: float,
        signal: Signal | None,
        regime: str,
    ) -> FillResult:
        bar_key = bar_time.isoformat()
        if self.state.last_bar_time == bar_key:
            return FillResult("skip", "Already processed this bar")

        self.state.last_bar_time = bar_key

        if self.state.open_position is not None:
            result = self._check_exit(high, low, close, bar_time)
            if result.action == "close":
                return result

        if self.state.open_position is None and signal is not None:
            return self._open(signal, close, bar_time)

        return FillResult("hold", f"No action (regime={regime})")

    def _open(self, signal: Signal, price: float, bar_time: datetime) -> FillResult:
        cfg = self.config
        risk_amount = self.state.equity * cfg.risk_per_trade
        stop_distance = abs(price - signal.stop_loss)
        if stop_distance <= 0:
            return FillResult("skip", "Invalid stop distance")

        if getattr(cfg, "spread_check_enabled", True):
            check = get_spread_monitor().check_trade(cfg.symbol, price, stop_distance)
            if not check.tradeable:
                return FillResult(
                    "skip",
                    f"Spread blocked: {check.current_spread_pct*100:.3f}% — {check.reason}",
                )

        size = risk_amount / stop_distance
        commission = price * size * cfg.commission_pct
        self.state.equity -= commission

        self.state.open_position = OpenPosition(
            side=signal.side.value,
            entry_time=bar_time.isoformat(),
            entry_price=price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            size=size,
            reason=signal.reason,
        )
        msg = (
            f"OPEN {signal.side.value} @ {price:.2f} "
            f"SL={signal.stop_loss:.2f} TP={signal.take_profit:.2f} ({signal.reason})"
        )
        append_log(self.log_path, msg)
        return FillResult("open", msg)

    def _check_exit(self, high: float, low: float, close: float, bar_time: datetime) -> FillResult:
        pos = self.state.open_position
        assert pos is not None

        exit_price: float | None = None
        exit_reason = ""

        if pos.side == Side.LONG.value:
            if low <= pos.stop_loss:
                exit_price, exit_reason = pos.stop_loss, "stop_loss"
            elif high >= pos.take_profit:
                exit_price, exit_reason = pos.take_profit, "take_profit"
        else:
            if high >= pos.stop_loss:
                exit_price, exit_reason = pos.stop_loss, "stop_loss"
            elif low <= pos.take_profit:
                exit_price, exit_reason = pos.take_profit, "take_profit"

        if exit_price is None:
            return FillResult("hold", "Position open")

        gross = (
            (exit_price - pos.entry_price) * pos.size
            if pos.side == Side.LONG.value
            else (pos.entry_price - exit_price) * pos.size
        )
        commission = (pos.entry_price + exit_price) * pos.size * self.config.commission_pct
        pnl = gross - commission
        self.state.equity += pnl
        if self.state.equity > self.state.peak_equity:
            self.state.peak_equity = self.state.equity
        self.state.trade_count += 1
        self.state.open_position = None

        msg = (
            f"CLOSE {pos.side} @ {exit_price:.2f} ({exit_reason}) "
            f"PnL={pnl:.2f} equity={self.state.equity:.2f}"
        )
        append_log(self.log_path, msg)
        return FillResult("close", msg, pnl=pnl)
