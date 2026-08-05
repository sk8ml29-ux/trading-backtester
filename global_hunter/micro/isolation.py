"""IsolatedExecutionWrapper: the fault-isolation shell required around every
micro-agent so that a crash, hang, or API error in ONE strategy can never
take down the rest of the swarm (or the process).

Guarantees:
  - Every exception raised by the wrapped module's `scan()` -- including a
    hang, via an explicit timeout -- is caught HERE and turned into
    circuit-breaker state instead of propagating up into the shared
    asyncio.gather() that runs the whole swarm.
  - Repeated failures trip a circuit breaker (CLOSED -> OPEN) that stops
    calling the module for an exponentially growing cooldown (saving API
    quota / not hammering a broken endpoint), then probes it once
    (HALF_OPEN) before fully re-enabling it.
  - Every attempt reports a HealthSnapshot to an optional callback --
    CapitalAllocator wires itself in here to throttle capital to an
    unhealthy module in real time, with zero polling / zero extra I/O.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from global_hunter.contracts import Opportunity
from global_hunter.scanner.base import UniversalValueModule

logger = logging.getLogger("global_hunter.micro.isolation")


class CircuitState(str, Enum):
    CLOSED = "closed"        # healthy, scanning normally
    OPEN = "open"             # tripped -- not calling the module, cooling down
    HALF_OPEN = "half_open"   # cooldown elapsed -- probing with a single attempt


@dataclass(frozen=True)
class HealthSnapshot:
    module_name: str
    state: CircuitState
    total_scans: int
    total_failures: int
    consecutive_failures: int
    consecutive_successes: int
    last_error: str | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None

    @property
    def is_healthy(self) -> bool:
        return self.state != CircuitState.OPEN


OnHealthChange = Callable[[HealthSnapshot], None]


class IsolatedExecutionWrapper:
    def __init__(
        self,
        module: UniversalValueModule,
        on_health_change: OnHealthChange | None = None,
        failure_threshold: int = 3,
        base_cooldown_s: float = 30.0,
        max_cooldown_s: float = 1800.0,
        scan_timeout_s: float = 30.0,
    ) -> None:
        self.module = module
        self.on_health_change = on_health_change
        self.failure_threshold = failure_threshold
        self.base_cooldown_s = base_cooldown_s
        self.max_cooldown_s = max_cooldown_s
        self.scan_timeout_s = scan_timeout_s

        self._state = CircuitState.CLOSED
        self._total_scans = 0
        self._total_failures = 0
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_error: str | None = None
        self._last_attempt_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._cooldown_s = base_cooldown_s
        self._opened_at_monotonic: float | None = None

    @property
    def name(self) -> str:
        return self.module.name

    @property
    def health(self) -> HealthSnapshot:
        return HealthSnapshot(
            module_name=self.module.name, state=self._state,
            total_scans=self._total_scans, total_failures=self._total_failures,
            consecutive_failures=self._consecutive_failures, consecutive_successes=self._consecutive_successes,
            last_error=self._last_error, last_attempt_at=self._last_attempt_at, last_success_at=self._last_success_at,
        )

    def _emit_health(self) -> None:
        if self.on_health_change is None:
            return
        try:
            self.on_health_change(self.health)
        except Exception:  # noqa: BLE001 - a broken callback must never break the isolation boundary itself
            logger.exception("on_health_change callback raised for %s", self.module.name)

    async def _attempt_scan(self) -> list[Opportunity]:
        self._last_attempt_at = datetime.now(timezone.utc)
        self._total_scans += 1
        try:
            opportunities = await asyncio.wait_for(self.module.scan(), timeout=self.scan_timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - THIS is the isolation boundary: nothing escapes past here
            self._total_failures += 1
            self._consecutive_failures += 1
            self._consecutive_successes = 0
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._on_failure()
            self._emit_health()
            return []
        else:
            self._consecutive_failures = 0
            self._consecutive_successes += 1
            self._last_success_at = datetime.now(timezone.utc)
            self._on_success()
            self._emit_health()
            return opportunities

    def _on_failure(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._cooldown_s = min(self._cooldown_s * 2, self.max_cooldown_s)
            self._trip_open()
        elif self._consecutive_failures >= self.failure_threshold:
            self._trip_open()

    def _trip_open(self) -> None:
        if self._state != CircuitState.OPEN:
            logger.warning(
                "CIRCUIT OPEN for %s after %d consecutive failures (last error: %s) -- cooling down %.0fs",
                self.module.name, self._consecutive_failures, self._last_error, self._cooldown_s,
            )
        self._state = CircuitState.OPEN
        self._opened_at_monotonic = time.monotonic()

    def _on_success(self) -> None:
        if self._state != CircuitState.CLOSED:
            logger.info("CIRCUIT CLOSED for %s after a successful probe", self.module.name)
        self._state = CircuitState.CLOSED
        self._cooldown_s = self.base_cooldown_s

    def _ready_for_probe(self) -> bool:
        if self._opened_at_monotonic is None:
            return True
        return (time.monotonic() - self._opened_at_monotonic) >= self._cooldown_s

    async def run_forever(self, queue: "asyncio.Queue[Opportunity]") -> None:
        try:
            while True:
                if self._state == CircuitState.OPEN:
                    if self._ready_for_probe():
                        self._state = CircuitState.HALF_OPEN
                    else:
                        await asyncio.sleep(min(5.0, self._cooldown_s))
                        continue

                for opportunity in await self._attempt_scan():
                    await queue.put(opportunity)

                sleep_s = self.module.poll_interval_s if self._state == CircuitState.CLOSED else self._cooldown_s
                await asyncio.sleep(sleep_s)
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        await self.module.aclose()
