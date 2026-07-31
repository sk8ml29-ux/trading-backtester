"""MicroAgent: base class for every independent, isolated micro-strategy in
the diversified swarm (see global_hunter/micro/registry.py for the full
portfolio + the "why does this have a statistical edge" explanation for
each one, and global_hunter/micro/agents/ for the implementations).

A MicroAgent IS a UniversalValueModule -- it plugs into the same
Opportunity contract as the five macro scanner modules -- but is always run
through IsolatedExecutionWrapper (never bare `run_forever()`) and always
registered with a CapitalAllocator, per the diversification mandate: many
small, independently-failing units instead of a few large ones.
"""

from __future__ import annotations

from dataclasses import dataclass

from global_hunter.scanner.base import UniversalValueModule


@dataclass(frozen=True)
class MicroAgentProfile:
    name: str
    asset_class: str
    edge_rationale: str
    target_daily_sek: float


class MicroAgent(UniversalValueModule):
    """Adds the two pieces of metadata the entrepreneur asked every module
    to state explicitly: WHY it has an edge, and what its (deliberately
    small) daily target is. This is documentation-as-code, not business
    logic -- `profile()` is what the "presentera din portfolj" report and
    any future dashboard reads from.
    """

    asset_class: str = "unspecified"
    edge_rationale: str = ""
    target_daily_sek: float = 125.0

    def profile(self) -> MicroAgentProfile:
        return MicroAgentProfile(
            name=self.name, asset_class=self.asset_class,
            edge_rationale=self.edge_rationale, target_daily_sek=self.target_daily_sek,
        )
