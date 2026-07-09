from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from backtest.data_loader import fetch_ohlcv
from backtest.forex_loader import is_forex_symbol, load_forex_entry, load_forex_regime
from backtest.mtf import apply_regime_to_entry, prepare_entry_frame
from backtest.optimized_loader import apply_to_config
from config import BacktestConfig, LiveConfig
from live.paper_broker import PaperBroker
from live.state import BotState, append_log
from strategies import STRATEGIES


class LiveRunner:
    def __init__(self, config: LiveConfig, optimized: bool = False):
        self.config = config
        self.optimized = optimized
        self.state_path = Path(config.state_file)
        self.state = BotState.load(
            self.state_path,
            default_equity=config.initial_capital,
            symbol=config.symbol,
            strategy=config.strategy,
        )
        self.broker = PaperBroker(config, self.state)
        self._strategy = None
        self._bt_cfg: BacktestConfig | None = None

    def _backtest_config(self) -> BacktestConfig:
        if self._bt_cfg is None:
            cfg = self.config.to_backtest_config()
            if self.optimized and not is_forex_symbol(self.config.symbol):
                apply_to_config(cfg, self.config.strategy)
            self._bt_cfg = cfg
        return self._bt_cfg

    def _strategy_instance(self):
        if self._strategy is None:
            self._strategy = STRATEGIES[self.config.strategy](self._backtest_config())
        return self._strategy

    def fetch_market_data(self, refresh: bool = True) -> pd.DataFrame:
        cfg = self._backtest_config()
        entry_tf = cfg.entry_timeframe or cfg.timeframe
        regime_tf = cfg.regime_timeframe

        if is_forex_symbol(self.config.symbol):
            entry_df = load_forex_entry(
                self.config.symbol, entry_tf, refresh=False
            )
            regime_df = load_forex_regime(self.config.symbol)
        elif entry_tf == regime_tf:
            return fetch_ohlcv(
                self.config.symbol,
                timeframe=entry_tf,
                start="2015-01-01",
                refresh=refresh,
            )
        else:
            entry_df = fetch_ohlcv(
                self.config.symbol,
                timeframe=entry_tf,
                start="2015-01-01",
                refresh=refresh,
            )
            regime_df = fetch_ohlcv(
                self.config.symbol,
                timeframe=regime_tf,
                start="2015-01-01",
                refresh=refresh,
            )

        if entry_tf == regime_tf and not is_forex_symbol(self.config.symbol):
            return entry_df

        entry_frame = prepare_entry_frame(entry_df, cfg)
        return apply_regime_to_entry(entry_frame, regime_df, cfg)

    def evaluate_latest(self, df: pd.DataFrame | None = None) -> dict:
        data = df if df is not None else self.fetch_market_data(refresh=True)
        strategy = self._strategy_instance()
        prepared = strategy.prepare(data)

        if len(prepared) < 3:
            return {"status": "error", "message": "Not enough bars"}

        row = prepared.iloc[-1]
        prev = prepared.iloc[-2]
        bar_time = prepared.index[-1]
        regime = str(row.get("regime", "range"))

        signal = None
        if strategy.allows_regime(regime) and self.state.open_position is None:
            signal = strategy.generate_signal(row, prev)

        result = self.broker.on_bar(
            bar_time=bar_time,
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            signal=signal,
            regime=regime,
        )

        self.state.save(self.state_path)

        return {
            "status": result.action,
            "message": result.message,
            "symbol": self.config.symbol,
            "strategy": self.config.strategy,
            "bar_time": bar_time.isoformat(),
            "close": float(row["close"]),
            "regime": regime,
            "equity": self.state.equity,
            "open_position": self.state.open_position,
            "trade_count": self.state.trade_count,
            "pnl": result.pnl,
        }

    def run_loop(self, once: bool = False) -> None:
        log_path = Path(self.config.log_file)
        append_log(
            log_path,
            f"Starting paper bot: {self.config.strategy} on {self.config.symbol} "
            f"({self.config.timeframe}, optimized={self.optimized})",
        )

        while True:
            try:
                outcome = self.evaluate_latest()
                line = (
                    f"{outcome['bar_time']} {outcome['symbol']} "
                    f"close={outcome['close']:.2f} regime={outcome['regime']} "
                    f"equity={outcome['equity']:.2f} "
                    f"-> {outcome['status']}: {outcome['message']}"
                )
                print(line)
                append_log(log_path, line)
            except Exception as exc:
                err = f"Error: {exc}"
                print(err)
                append_log(log_path, err)

            if once:
                break
            time.sleep(self.config.poll_seconds)
