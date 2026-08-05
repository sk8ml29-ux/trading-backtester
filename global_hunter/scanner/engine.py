"""UniversalAnomalyScanner: runs an unbounded list of UniversalValueModule
instances concurrently and funnels every Opportunity onto one shared queue.

Adding a new market = register one more module here. This file never needs
to know what "crypto", "energy", or "prediction market" even mean.
"""

from __future__ import annotations

import asyncio
import logging

from global_hunter.contracts import Opportunity
from global_hunter.scanner.base import UniversalValueModule

logger = logging.getLogger("global_hunter.scanner.engine")


class UniversalAnomalyScanner:
    def __init__(
        self,
        modules: list[UniversalValueModule],
        queue: "asyncio.Queue[Opportunity] | None" = None,
    ) -> None:
        if not modules:
            raise ValueError("UniversalAnomalyScanner needs at least one module")
        self.modules = modules
        self.queue: "asyncio.Queue[Opportunity]" = queue if queue is not None else asyncio.Queue()

    async def run(self) -> None:
        """Run every module's supervised loop forever, in parallel."""
        tasks = [
            asyncio.create_task(module.run_forever(self.queue), name=f"scan:{module.name}")
            for module in self.modules
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def scan_once(self) -> list[Opportunity]:
        """One-shot concurrent scan across all modules. Used for dry runs / CLI."""
        results = await asyncio.gather(
            *(module.scan() for module in self.modules), return_exceptions=True
        )
        opportunities: list[Opportunity] = []
        for module, result in zip(self.modules, results):
            if isinstance(result, BaseException):
                logger.warning("%s: scan_once failed: %s", module.name, result)
                continue
            opportunities.extend(result)
        return opportunities

    async def aclose(self) -> None:
        await asyncio.gather(*(m.aclose() for m in self.modules), return_exceptions=True)
