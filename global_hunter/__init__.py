"""Global Value Hunter & Arbitrage Bot.

Three independent, queue-coupled modules (see AGENTS.md / .cursorrules for the
full architecture mandate):

  scanner    -> UniversalAnomalyScanner   (finds Opportunity objects)
  legal      -> LegalAndTaxFilter         (approves/rejects, sizes, computes net-of-tax profit)
  engine     -> DynamicExecutionEngine    (places orders via pluggable adapters)

Everything communicates through the dataclasses in `global_hunter.contracts`.
No module imports the internals of another module.
"""

from __future__ import annotations

__all__ = ["contracts", "config"]
