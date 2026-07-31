"""MicroAgentSwarm: runs the whole diversified portfolio of MicroAgents,
each behind its own IsolatedExecutionWrapper, all health-reported into one
CapitalAllocator. Feeds a shared Opportunity queue -- pass the SAME queue
UniversalAnomalyScanner uses (`scanner.queue`) so micro-agent opportunities
flow through the exact same LegalAndTaxFilter -> ExecutionGovernor ->
DynamicExecutionEngine pipeline as the five macro modules.
"""

from __future__ import annotations

import asyncio
import logging

from global_hunter.contracts import Opportunity
from global_hunter.micro.allocator import CapitalAllocator
from global_hunter.micro.base import MicroAgent, MicroAgentProfile
from global_hunter.micro.isolation import HealthSnapshot, IsolatedExecutionWrapper

logger = logging.getLogger("global_hunter.micro.swarm")


class MicroAgentSwarm:
    def __init__(
        self,
        agents: list[MicroAgent],
        allocator: CapitalAllocator,
        queue: "asyncio.Queue[Opportunity] | None" = None,
    ) -> None:
        self.agents = agents
        self.allocator = allocator
        self.queue: "asyncio.Queue[Opportunity]" = queue if queue is not None else asyncio.Queue()
        self.wrappers: list[IsolatedExecutionWrapper] = [
            IsolatedExecutionWrapper(agent, on_health_change=allocator.report_health) for agent in agents
        ]

    def portfolio(self) -> list[MicroAgentProfile]:
        return [agent.profile() for agent in self.agents]

    async def run(self) -> None:
        tasks = [
            asyncio.create_task(wrapper.run_forever(self.queue), name=f"micro:{wrapper.name}")
            for wrapper in self.wrappers
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def scan_once(self) -> list[Opportunity]:
        """One-shot pass across the whole swarm -- still fully isolated per
        agent (a crashing agent contributes zero opportunities, nothing more)."""
        results = await asyncio.gather(*(w._attempt_scan() for w in self.wrappers), return_exceptions=True)
        opportunities: list[Opportunity] = []
        for wrapper, result in zip(self.wrappers, results):
            if isinstance(result, BaseException):
                logger.warning("%s: scan_once failed outside the isolation boundary (bug?): %s", wrapper.name, result)
                continue
            opportunities.extend(result)
        return opportunities

    def health_report(self) -> dict[str, HealthSnapshot]:
        return {wrapper.name: wrapper.health for wrapper in self.wrappers}

    async def aclose(self) -> None:
        await asyncio.gather(*(w.aclose() for w in self.wrappers), return_exceptions=True)
