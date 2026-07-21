"""
Alpaca-exekvering — kör strategier mot en riktig mäklare.
=========================================================
Återanvänder strateilogiken (prepare/generate_signal) och lägger RIKTIGA
bracket-order via Alpaca istället för att simulera. Mäklaren sköter exit
(stop-loss / take-profit server-side).

Flöde per par:
  1. Hämta senaste marknadsdata + kör strategin
  2. Kolla om vi redan har en position hos mäklaren (då: gör inget)
  3. Om ny signal + flat + Gatekeeper OK + marknaden öppen:
       beräkna storlek från kontots equity (risk% av equity / stop-avstånd)
       lägg bracket-order (entry + SL + TP)
  4. Logga allt

Säkerhet:
  - Gatekeepern kapar risk/trade (2%) och kan halta allt vid stor drawdown.
  - Default paper-miljö. Live kräver ALPACA_ENV=live.
  - Hälsovakten kan pausa degraderade strategier (samma som paper).
"""
from __future__ import annotations

from pathlib import Path

from backtest.optimized_loader import apply_optimized_to_live
from config import LiveConfig
from live.alpaca_broker import AlpacaBroker
from live.runner import LiveRunner
from live.state import append_log
from strategies.base import Side

try:
    from risk.gatekeeper import RiskGatekeeper
    _GATEKEEPER = RiskGatekeeper()
except Exception:
    _GATEKEEPER = None

GATEKEEPER_MAX_RISK = 0.02


class AlpacaExecutor:
    def __init__(self, broker: AlpacaBroker, log_path: Path | None = None):
        self.broker = broker
        self.log_path = log_path or Path("data/live/alpaca_exec.log")

    def _log(self, msg: str) -> None:
        append_log(self.log_path, msg)

    def evaluate_and_execute(self, cfg: LiveConfig, params: dict | None = None) -> dict:
        """Utvärdera ett par och lägg order om läge finns."""
        symbol = cfg.symbol
        # Återanvänd LiveRunner för datainhämtning + signal (men handla ej i paper)
        runner = LiveRunner(cfg, optimized=True)
        if params:
            for k, v in params.items():
                if hasattr(runner._backtest_config(), k):
                    setattr(runner._backtest_config(), k, v)

        # Har vi redan en position hos mäklaren?
        existing = self.broker.get_position(symbol)
        if existing:
            return {"symbol": symbol, "action": "hold", "message": "position finns redan hos mäklaren"}

        # Hälsopaus?
        if runner._is_health_paused():
            return {"symbol": symbol, "action": "skip", "message": "hälsovakt: pausad (degraderad)"}

        # Kör strategin på senaste data
        try:
            data = runner.fetch_market_data(refresh=True)
            strategy = runner._strategy_instance()
            prepared = strategy.prepare(data)
            if len(prepared) < 3:
                return {"symbol": symbol, "action": "skip", "message": "för lite data"}
            row, prev = prepared.iloc[-1], prepared.iloc[-2]
            regime = str(row.get("regime", "range"))
            if not strategy.allows_regime(regime):
                return {"symbol": symbol, "action": "hold", "message": f"regim {regime} tillåts ej"}
            signal = strategy.generate_signal(row, prev)
        except Exception as e:
            return {"symbol": symbol, "action": "error", "message": f"strategifel: {e}"}

        if signal is None:
            return {"symbol": symbol, "action": "hold", "message": "ingen signal"}

        # Konto & marknad
        account = self.broker.get_account()
        if not account:
            return {"symbol": symbol, "action": "error", "message": "kunde ej hämta konto"}
        equity = float(account.get("equity", 0))
        if equity <= 0:
            return {"symbol": symbol, "action": "error", "message": "equity 0"}

        close = float(row["close"])
        stop_distance = abs(close - signal.stop_loss)
        if stop_distance <= 0:
            return {"symbol": symbol, "action": "skip", "message": "ogiltigt stop-avstånd"}

        # Risk med conviction-multiplikator, kapad av Gatekeepern
        risk_mult = max(0.1, float(getattr(signal, "risk_mult", 1.0)))
        effective_risk = min(cfg.risk_per_trade * risk_mult, GATEKEEPER_MAX_RISK)

        # Gatekeeper-koll
        if _GATEKEEPER is not None:
            peak = float(account.get("equity", equity))
            verdict = _GATEKEEPER.check_new_trade(
                symbol=symbol, risk_pct=effective_risk, equity=equity,
                peak_equity=peak, daily_start_equity=equity,
                open_positions=self.broker.get_positions(),
            )
            if not verdict.allowed:
                self._log(f"GATEKEEPER blockerade {symbol}: {verdict.reason}")
                return {"symbol": symbol, "action": "blocked", "message": verdict.reason}

        risk_amount = equity * effective_risk
        qty = risk_amount / stop_distance
        if qty < 1:
            return {"symbol": symbol, "action": "skip", "message": f"qty {qty:.2f} < 1 aktie"}

        side = "buy" if signal.side == Side.LONG else "sell"
        result = self.broker.submit_bracket(
            symbol=symbol, qty=qty, side=side,
            stop_loss=signal.stop_loss, take_profit=signal.take_profit,
            client_order_id=f"{symbol}_{cfg.strategy}_{int(close)}",
        )
        conv = f" [risk_mult={risk_mult}]" if risk_mult > 1.0 else ""
        self._log(f"{symbol}/{cfg.strategy} {side} qty={int(qty)} "
                  f"SL={signal.stop_loss:.2f} TP={signal.take_profit:.2f} "
                  f"risk={effective_risk*100:.2f}%{conv} -> {result.message}")
        return {
            "symbol": symbol, "action": "order" if result.ok else "error",
            "message": result.message, "order_id": result.order_id,
            "qty": int(qty), "side": side, "risk_pct": effective_risk,
        }


def build_live_config(symbol: str, strategy: str, timeframe: str,
                      capital: float, risk: float, regime_tf: str = "1d") -> LiveConfig:
    cfg = LiveConfig(
        symbol=symbol, strategy=strategy, timeframe=timeframe,
        initial_capital=capital, risk_per_trade=risk, regime_timeframe=regime_tf,
    )
    return cfg
