"""CLI entry point for the Global Value Hunter & Arbitrage Bot.

Examples:
    python run_global_hunter.py --once                 # one scan+filter+governor pass, print decisions, no execution
    python run_global_hunter.py --once --capital 400000
    python run_global_hunter.py --serve --capital 400000  # run forever (paper execution by default)
    python -m global_hunter.model                      # print the aggregated 30k SEK/month math model

Defaults to the PaperExecutionAdapter -- no real orders, no API keys needed,
matching the rest of this repo's "paper only" convention (see AGENTS.md).

Full pipeline: UniversalAnomalyScanner (GlobalMarketNeutralArbitrage +
PredictiveValueAccumulation + OpportunisticMarketScraper + GlobalIngestionEngine
+ AlphaEventScanner) -> LegalAndTaxFilter -> ExecutionGovernor -> DynamicExecutionEngine.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from global_hunter.config import CapitalLedger, MAX_CAPITAL_SEK, MIN_CAPITAL_SEK
from global_hunter.contracts import ApprovedOrder
from global_hunter.engine.adapters.paper import PaperExecutionAdapter
from global_hunter.engine.engine import DynamicExecutionEngine
from global_hunter.events.engine import AlphaEventScanner
from global_hunter.governor.engine import ExecutionGovernor
from global_hunter.ingestion.engine import GlobalIngestionEngine
from global_hunter.legal.engine import LegalAndTaxFilter
from global_hunter.orchestrator import GlobalValueHunter
from global_hunter.scanner.arbitrage import GlobalMarketNeutralArbitrage
from global_hunter.scanner.engine import UniversalAnomalyScanner
from global_hunter.scanner.scraper import OpportunisticMarketScraper, default_targets as default_scrape_targets
from global_hunter.scanner.value_accumulation import PredictiveValueAccumulation


def build_hunter(capital_sek: float) -> GlobalValueHunter:
    scanner = UniversalAnomalyScanner(
        modules=[
            GlobalMarketNeutralArbitrage(),
            PredictiveValueAccumulation(),
            OpportunisticMarketScraper(default_scrape_targets()),
            GlobalIngestionEngine(),
            AlphaEventScanner(),
        ]
    )
    legal_filter = LegalAndTaxFilter()
    ledger = CapitalLedger(total_capital_sek=capital_sek)
    execution_engine = DynamicExecutionEngine(
        adapters={"paper": PaperExecutionAdapter()}, default_adapter="paper",
    )
    governor = ExecutionGovernor(ledger=ledger, execution_engine=execution_engine)
    return GlobalValueHunter(scanner, legal_filter, governor, execution_engine, ledger)


async def main_async(args: argparse.Namespace) -> int:
    hunter = build_hunter(args.capital)
    try:
        if args.once:
            decisions = await hunter.run_once()
            approved = [d for d in decisions if isinstance(d, ApprovedOrder)]
            print(f"Scan complete: {len(decisions)} opportunities, {len(approved)} approved.")
            for d in decisions:
                if isinstance(d, ApprovedOrder):
                    print(
                        f"  APPROVED {d.opportunity.source:<32} {d.opportunity.instrument:<18} "
                        f"size={d.size_sek:>9,.0f} SEK  net_profit={d.expected_net_profit_sek:>8,.2f} SEK"
                    )
                else:
                    print(f"  REJECTED {d.opportunity.source:<32} {d.opportunity.instrument:<18} reason={d.reason}")
            return 0
        else:
            print(f"Running forever with {args.capital:,.0f} SEK capital (Ctrl+C to stop)...")
            await hunter.run_forever()
            return 0
    finally:
        await hunter.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Global Value Hunter & Arbitrage Bot")
    parser.add_argument("--capital", type=float, default=200_000.0, help=f"Total capital in SEK ({MIN_CAPITAL_SEK:.0f}-{MAX_CAPITAL_SEK:.0f})")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Single scan+filter+governor pass, no execution loop")
    mode.add_argument("--serve", action="store_true", help="Run scanner+filter+governor+execution forever (paper adapter)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
