"""CapitalAllocator: real-time health- and performance-based capital
throttle across independent micro-modules (and, if wired in, the five
macro scanner modules too -- it works off `Opportunity.source` strings,
it doesn't care which package emitted them).

Consulted by LegalAndTaxFilter at sizing time via `get_multiplier(source)`.
It NEVER grants more than a bounded ceiling above the normal per-trade cap
(default 1.5x) -- it only ever takes capital away from bad performers and
gives some of it back to good ones, always still inside the diversification
cap (`LegalConfig.max_position_pct_of_capital`) and the hard 100k-800k SEK
capital fence enforced by CapitalLedger. This is a THROTTLE, not a second
place leverage can sneak in.

Two independent inputs feed the score, and the worse one always wins:
  1. Health (from IsolatedExecutionWrapper): a module whose circuit is OPEN
     is throttled to 0 immediately, regardless of how good its historical
     PnL looks -- an erroring module might be reporting stale/wrong data.
  2. Performance (rolling realized PnL + win/loss streaks): a module that
     is genuinely losing money gets throttled down even if it's
     technically "healthy" (no errors, just a bad edge or a bad market
     regime); a module on a genuine winning streak gets a modest capital
     increase, rewarding it with more of the shared pool.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from global_hunter.micro.isolation import CircuitState, HealthSnapshot

logger = logging.getLogger("global_hunter.micro.allocator")


@dataclass
class ModuleScore:
    name: str
    multiplier: float = 1.0
    rolling_pnl_sek: float = 0.0
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    last_health: HealthSnapshot | None = None
    last_updated_at: datetime | None = None
    pnl_history: list[float] = field(default_factory=list)


class CapitalAllocator:
    def __init__(
        self,
        performance_window: int = 20,
        loss_streak_floor_multiplier: float = 0.10,
        win_streak_ceiling_multiplier: float = 1.5,
        pnl_throttle_threshold_sek: float = -300.0,
        half_open_multiplier: float = 0.25,
    ) -> None:
        self.performance_window = performance_window
        self.loss_streak_floor_multiplier = loss_streak_floor_multiplier
        self.win_streak_ceiling_multiplier = win_streak_ceiling_multiplier
        self.pnl_throttle_threshold_sek = pnl_throttle_threshold_sek
        self.half_open_multiplier = half_open_multiplier
        self._scores: dict[str, ModuleScore] = {}

    def _score(self, module_name: str) -> ModuleScore:
        if module_name not in self._scores:
            self._scores[module_name] = ModuleScore(name=module_name)
        return self._scores[module_name]

    def report_health(self, health: HealthSnapshot) -> None:
        """Wire this in as an IsolatedExecutionWrapper's `on_health_change`."""
        score = self._score(health.module_name)
        score.last_health = health
        score.last_updated_at = datetime.now(timezone.utc)
        self._recompute(score)

    def report_result(self, module_name: str, realized_sek: float) -> None:
        """Call once per settled (filled/closed) ExecutionResult."""
        score = self._score(module_name)
        score.pnl_history.append(realized_sek)
        if len(score.pnl_history) > self.performance_window:
            score.pnl_history.pop(0)
        score.rolling_pnl_sek = sum(score.pnl_history)
        if realized_sek > 0:
            score.consecutive_wins += 1
            score.consecutive_losses = 0
        elif realized_sek < 0:
            score.consecutive_losses += 1
            score.consecutive_wins = 0
        score.last_updated_at = datetime.now(timezone.utc)
        self._recompute(score)

    def _recompute(self, score: ModuleScore) -> None:
        previous = score.multiplier
        if score.last_health is not None and score.last_health.state == CircuitState.OPEN:
            score.multiplier = 0.0
        elif score.last_health is not None and score.last_health.state == CircuitState.HALF_OPEN:
            score.multiplier = self.half_open_multiplier
        elif score.rolling_pnl_sek < self.pnl_throttle_threshold_sek:
            score.multiplier = max(self.loss_streak_floor_multiplier, 1.0 - 0.15 * score.consecutive_losses)
        elif score.consecutive_wins >= 3:
            score.multiplier = min(self.win_streak_ceiling_multiplier, 1.0 + 0.1 * (score.consecutive_wins - 2))
        else:
            score.multiplier = 1.0

        if abs(score.multiplier - previous) > 1e-9:
            logger.info(
                "CapitalAllocator: %s multiplier %.2f -> %.2f (pnl=%.2f SEK, wins=%d, losses=%d, circuit=%s)",
                score.name, previous, score.multiplier, score.rolling_pnl_sek,
                score.consecutive_wins, score.consecutive_losses,
                score.last_health.state.value if score.last_health else "unknown",
            )

    def get_multiplier(self, module_name: str) -> float:
        return self._score(module_name).multiplier

    def snapshot(self) -> dict[str, dict]:
        """For dashboards/logging: current allocator state per module."""
        return {
            name: {
                "multiplier": s.multiplier,
                "rolling_pnl_sek": s.rolling_pnl_sek,
                "consecutive_wins": s.consecutive_wins,
                "consecutive_losses": s.consecutive_losses,
                "circuit_state": s.last_health.state.value if s.last_health else "unknown",
            }
            for name, s in self._scores.items()
        }
