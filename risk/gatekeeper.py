"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                         THE GATEKEEPER                                      ║
║                  Hardcoded Risk Module — DO NOT MODIFY                      ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  THIS FILE IS IMMUTABLE INFRASTRUCTURE.                                     ║
║                                                                             ║
║  • No AI-generated code may import, monkey-patch, or override the          ║
║    constants defined below.                                                 ║
║  • The lab/lab_runner.py calls check_strategy_promotion() — if this        ║
║    function returns allowed=False, promotion is ABORTED, full stop.        ║
║  • The live runner calls check_new_trade() before every order — if         ║
║    this returns severity="halt", ALL trading stops immediately.            ║
║  • Any change here requires a human commit + backup confirmation.          ║
╚══════════════════════════════════════════════════════════════════════════════╝

Controls enforced:
  Live trading
  ├── MAX_PORTFOLIO_DRAWDOWN_PCT  20 %  — halt all bots
  ├── MAX_DAILY_LOSS_PCT           5 %  — halt for the day
  ├── MAX_SINGLE_POSITION_RISK_PCT 2 %  — hard cap per trade
  └── MAX_CORRELATED_POSITIONS     3    — same asset-class limit

  Strategy promotion (lab → production)
  ├── MIN_OOS_DAYS               365    — minimum 1 year OOS window
  ├── MIN_OOS_TRADES              30    — statistical significance
  ├── MIN_OOS_PROFIT_FACTOR      1.25   — minimum profitability
  ├── MIN_OOS_SHARPE             0.80   — risk-adjusted return floor
  ├── MAX_OOS_DRAWDOWN_PCT       25 %   — OOS max drawdown ceiling
  └── PROMOTION_IMPROVEMENT      10 %   — must beat incumbent by ≥10 %
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# IMMUTABLE LIMITS  ——  NEVER CHANGE THESE WITHOUT HUMAN REVIEW + BACKUP
# ─────────────────────────────────────────────────────────────────────────────

MAX_PORTFOLIO_DRAWDOWN_PCT: float = 0.20        # 20 % — triggers full halt
MAX_DAILY_LOSS_PCT: float = 0.05                # 5 %  — day circuit-breaker
MAX_SINGLE_POSITION_RISK_PCT: float = 0.02      # 2 %  — per-trade hard cap
MAX_CORRELATED_POSITIONS: int = 3               # max open in same asset class

MIN_OOS_DAYS: int = 365                         # minimum OOS test window
MIN_OOS_TRADES: int = 30                        # min trades for significance
MIN_OOS_PROFIT_FACTOR: float = 1.25             # minimum OOS profit factor
MIN_OOS_SHARPE: float = 0.80                    # minimum OOS Sharpe ratio
MAX_OOS_DRAWDOWN_PCT: float = 0.25              # OOS drawdown ceiling
PROMOTION_IMPROVEMENT_THRESHOLD: float = 0.10  # candidate vs incumbent delta

# Asset-class map for correlated-exposure checks
ASSET_CLASS_MAP: dict[str, str] = {
    # Crypto
    "BTC": "crypto",       "BTCUSDT": "crypto",   "BTC-USD": "crypto",
    "ETH": "crypto",       "ETHUSDT": "crypto",   "ETH-USD": "crypto",
    "SOL": "crypto",       "SOLUSDT": "crypto",   "SOL-USD": "crypto",
    "XRP": "crypto",       "XRPUSDT": "crypto",   "XRP-USD": "crypto",
    "ADA": "crypto",       "ADAUSDT": "crypto",   "ADA-USD": "crypto",
    "DOGE": "crypto",      "DOGEUSDT": "crypto",  "DOGE-USD": "crypto",
    "LINK": "crypto",      "LINKUSDT": "crypto",  "LINK-USD": "crypto",
    # Forex majors
    "EURUSD=X": "fx_eur",  "EURUSD": "fx_eur",
    "GBPUSD=X": "fx_gbp",  "GBPUSD": "fx_gbp",
    "USDJPY=X": "fx_jpy",  "USDJPY": "fx_jpy",
    "AUDUSD=X": "fx_aud",  "AUDUSD": "fx_aud",
    "USDCAD=X": "fx_cad",  "USDCAD": "fx_cad",
    "USDCHF=X": "fx_chf",  "USDCHF": "fx_chf",
    # Forex crosses
    "EURGBP=X": "fx_cross", "EURGBP": "fx_cross",
    "EURJPY=X": "fx_cross", "EURJPY": "fx_cross",
    "GBPJPY=X": "fx_cross", "GBPJPY": "fx_cross",
    # Commodities
    "GC=F": "commodity",   "SI=F": "commodity",
    "CL=F": "commodity",   "NG=F": "commodity",
}


# ─────────────────────────────────────────────────────────────────────────────
# VERDICT TYPE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GatekeeperVerdict:
    allowed: bool
    reason: str
    severity: Literal["ok", "warn", "block", "halt"]

    def __bool__(self) -> bool:
        return self.allowed


# ─────────────────────────────────────────────────────────────────────────────
# GATEKEEPER
# ─────────────────────────────────────────────────────────────────────────────

class RiskGatekeeper:
    """
    All public methods return a GatekeeperVerdict.

    Callers MUST:
      - Abort the trade / promotion if verdict.allowed is False
      - Log verdict.reason
      - Halt all bots if verdict.severity == "halt"
    """

    # ─────────────────────────────────────────────────────────────────────────
    # LIVE TRADE GATE
    # ─────────────────────────────────────────────────────────────────────────

    def check_new_trade(
        self,
        symbol: str,
        risk_pct: float,
        equity: float,
        peak_equity: float,
        daily_start_equity: float,
        open_positions: list[dict],
    ) -> GatekeeperVerdict:
        """
        Validate a proposed live trade.  Call this before every order entry.

        Args:
            symbol:               instrument symbol (e.g. "BTCUSDT", "EURUSD=X")
            risk_pct:             fraction of equity risked on this trade
            equity:               current account equity
            peak_equity:          highest recorded equity (for drawdown calc)
            daily_start_equity:   equity at start of today (for daily loss)
            open_positions:       list of dicts, each with key "symbol"
        """
        # 1. Portfolio drawdown circuit-breaker
        if peak_equity > 0:
            drawdown = (peak_equity - equity) / peak_equity
            if drawdown >= MAX_PORTFOLIO_DRAWDOWN_PCT:
                return GatekeeperVerdict(
                    allowed=False,
                    reason=f"HALT — portfolio drawdown {drawdown:.1%} ≥ {MAX_PORTFOLIO_DRAWDOWN_PCT:.0%} limit",
                    severity="halt",
                )

        # 2. Daily loss circuit-breaker
        if daily_start_equity > 0:
            daily_loss = (daily_start_equity - equity) / daily_start_equity
            if daily_loss >= MAX_DAILY_LOSS_PCT:
                return GatekeeperVerdict(
                    allowed=False,
                    reason=f"HALT — daily loss {daily_loss:.1%} ≥ {MAX_DAILY_LOSS_PCT:.0%} limit",
                    severity="halt",
                )

        # 3. Per-trade risk cap
        if risk_pct > MAX_SINGLE_POSITION_RISK_PCT:
            return GatekeeperVerdict(
                allowed=False,
                reason=f"BLOCK — trade risk {risk_pct:.2%} > {MAX_SINGLE_POSITION_RISK_PCT:.0%} hard cap",
                severity="block",
            )

        # 4. Correlated-exposure limit
        asset_class = ASSET_CLASS_MAP.get(symbol, f"unknown:{symbol}")
        correlated_count = sum(
            1
            for p in open_positions
            if ASSET_CLASS_MAP.get(p.get("symbol", ""), f"unknown:{p.get('symbol','')}") == asset_class
        )
        if correlated_count >= MAX_CORRELATED_POSITIONS:
            return GatekeeperVerdict(
                allowed=False,
                reason=(
                    f"BLOCK — {correlated_count} open positions in '{asset_class}' "
                    f"(max {MAX_CORRELATED_POSITIONS})"
                ),
                severity="block",
            )

        # 5. Approaching-limit warnings (non-blocking)
        if peak_equity > 0:
            drawdown = (peak_equity - equity) / peak_equity
            if drawdown >= MAX_PORTFOLIO_DRAWDOWN_PCT * 0.75:
                return GatekeeperVerdict(
                    allowed=True,
                    reason=f"WARN — drawdown {drawdown:.1%} approaching {MAX_PORTFOLIO_DRAWDOWN_PCT:.0%} limit",
                    severity="warn",
                )

        return GatekeeperVerdict(allowed=True, reason="ok", severity="ok")

    # ─────────────────────────────────────────────────────────────────────────
    # STRATEGY PROMOTION GATE
    # ─────────────────────────────────────────────────────────────────────────

    def check_strategy_promotion(
        self,
        candidate_metrics: dict[str, Any],
        incumbent_metrics: dict[str, Any],
        oos_days: int,
    ) -> GatekeeperVerdict:
        """
        Gate a lab candidate before promoting it to production.

        Args:
            candidate_metrics:  OOS metrics dict from LabEvaluator
            incumbent_metrics:  current live strategy metrics (same symbol/TF)
            oos_days:           number of calendar days in OOS test window
        """
        n_trades = int(candidate_metrics.get("n_trades", 0))
        pf = float(candidate_metrics.get("profit_factor", 0.0))
        sharpe = float(candidate_metrics.get("sharpe", 0.0))
        mdd = float(candidate_metrics.get("max_drawdown", 1.0))

        # 1. OOS window length
        if oos_days < MIN_OOS_DAYS:
            return GatekeeperVerdict(
                allowed=False,
                reason=f"BLOCK — OOS window {oos_days} days < {MIN_OOS_DAYS} day minimum",
                severity="block",
            )

        # 2. Statistical significance (trade count)
        if n_trades < MIN_OOS_TRADES:
            return GatekeeperVerdict(
                allowed=False,
                reason=f"BLOCK — only {n_trades} OOS trades, need ≥{MIN_OOS_TRADES}",
                severity="block",
            )

        # 3. Profit factor floor
        if pf < MIN_OOS_PROFIT_FACTOR:
            return GatekeeperVerdict(
                allowed=False,
                reason=f"BLOCK — OOS profit factor {pf:.2f} < {MIN_OOS_PROFIT_FACTOR}",
                severity="block",
            )

        # 4. Sharpe floor
        if sharpe < MIN_OOS_SHARPE:
            return GatekeeperVerdict(
                allowed=False,
                reason=f"BLOCK — OOS Sharpe {sharpe:.2f} < {MIN_OOS_SHARPE}",
                severity="block",
            )

        # 5. Drawdown ceiling
        if mdd > MAX_OOS_DRAWDOWN_PCT:
            return GatekeeperVerdict(
                allowed=False,
                reason=f"BLOCK — OOS max drawdown {mdd:.1%} > {MAX_OOS_DRAWDOWN_PCT:.0%} ceiling",
                severity="block",
            )

        # 6. Must beat incumbent by PROMOTION_IMPROVEMENT_THRESHOLD
        inc_sharpe = float(incumbent_metrics.get("sharpe", 0.0))
        inc_pf = float(incumbent_metrics.get("profit_factor", 1.0))

        sharpe_delta = (sharpe - inc_sharpe) / max(abs(inc_sharpe), 0.01)
        pf_delta = (pf - inc_pf) / max(inc_pf, 0.01)
        avg_improvement = (sharpe_delta + pf_delta) / 2.0

        if avg_improvement < PROMOTION_IMPROVEMENT_THRESHOLD:
            return GatekeeperVerdict(
                allowed=False,
                reason=(
                    f"BLOCK — improvement {avg_improvement:.1%} < {PROMOTION_IMPROVEMENT_THRESHOLD:.0%} "
                    f"(Sharpe {inc_sharpe:.2f}→{sharpe:.2f}, PF {inc_pf:.2f}→{pf:.2f})"
                ),
                severity="block",
            )

        return GatekeeperVerdict(
            allowed=True,
            reason=(
                f"PASS — Sharpe {sharpe:.2f} (+{sharpe_delta:.0%}), "
                f"PF {pf:.2f} (+{pf_delta:.0%}), "
                f"DD {mdd:.1%}, n={n_trades}"
            ),
            severity="ok",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # PORTFOLIO HEALTH PULSE
    # ─────────────────────────────────────────────────────────────────────────

    def check_portfolio_health(
        self,
        equity: float,
        peak_equity: float,
        daily_start_equity: float,
    ) -> GatekeeperVerdict:
        """
        Periodic health pulse — called by the cloud watchdog every 5 min.
        Returns halt if any hard limit is breached.
        """
        if peak_equity > 0:
            drawdown = (peak_equity - equity) / peak_equity
            if drawdown >= MAX_PORTFOLIO_DRAWDOWN_PCT:
                return GatekeeperVerdict(
                    allowed=False,
                    reason=f"HALT — drawdown {drawdown:.1%}",
                    severity="halt",
                )
            if drawdown >= MAX_PORTFOLIO_DRAWDOWN_PCT * 0.75:
                return GatekeeperVerdict(
                    allowed=True,
                    reason=f"WARN — drawdown {drawdown:.1%}, approaching {MAX_PORTFOLIO_DRAWDOWN_PCT:.0%}",
                    severity="warn",
                )

        if daily_start_equity > 0:
            daily_loss = (daily_start_equity - equity) / daily_start_equity
            if daily_loss >= MAX_DAILY_LOSS_PCT:
                return GatekeeperVerdict(
                    allowed=False,
                    reason=f"HALT — daily loss {daily_loss:.1%}",
                    severity="halt",
                )
            if daily_loss >= MAX_DAILY_LOSS_PCT * 0.75:
                return GatekeeperVerdict(
                    allowed=True,
                    reason=f"WARN — daily loss {daily_loss:.1%}, approaching {MAX_DAILY_LOSS_PCT:.0%}",
                    severity="warn",
                )

        return GatekeeperVerdict(allowed=True, reason="ok", severity="ok")
