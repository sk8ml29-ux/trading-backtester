"""
Candidate Store — persists lab candidates across three lifecycle stages:

  pending/    candidates under evaluation
  promoted/   candidates that passed the Gatekeeper and beat the incumbent
  rejected/   candidates that failed — kept for audit trail

Promoted candidates can be loaded by the live runner as alternative configs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
CANDIDATES_DIR = ROOT / "candidates"
PENDING_DIR   = CANDIDATES_DIR / "pending"
PROMOTED_DIR  = CANDIDATES_DIR / "promoted"
REJECTED_DIR  = CANDIDATES_DIR / "rejected"


@dataclass
class StrategyCandidate:
    candidate_id: str
    strategy_id: str
    symbol: str
    timeframe: str
    params: dict
    oos_metrics: dict
    score: float
    created_at: str
    promoted: bool = False
    promotion_verdict: str = ""
    deployed: bool = False          # True once loaded into the live runner


class CandidateStore:

    def __init__(self):
        for d in (PENDING_DIR, PROMOTED_DIR, REJECTED_DIR):
            d.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────────────────────
    # WRITE OPERATIONS
    # ─────────────────────────────────────────────────────────────────────────

    def save_pending(self, candidate: StrategyCandidate) -> Path:
        path = PENDING_DIR / f"{candidate.candidate_id}.json"
        path.write_text(json.dumps(asdict(candidate), indent=2))
        logger.debug("[CandidateStore] Saved pending: %s", candidate.candidate_id)
        return path

    def promote(self, candidate: StrategyCandidate, verdict: str) -> Path:
        candidate.promoted = True
        candidate.promotion_verdict = verdict
        path = PROMOTED_DIR / f"{candidate.candidate_id}.json"
        path.write_text(json.dumps(asdict(candidate), indent=2))

        # Remove from pending
        (PENDING_DIR / f"{candidate.candidate_id}.json").unlink(missing_ok=True)

        logger.info(
            "[CandidateStore] PROMOTED %s | %s/%s/%s | score=%.3f",
            candidate.candidate_id,
            candidate.strategy_id,
            candidate.symbol,
            candidate.timeframe,
            candidate.score,
        )
        return path

    def reject(self, candidate: StrategyCandidate, reason: str) -> Path:
        candidate.promotion_verdict = reason
        path = REJECTED_DIR / f"{candidate.candidate_id}.json"
        path.write_text(json.dumps(asdict(candidate), indent=2))
        (PENDING_DIR / f"{candidate.candidate_id}.json").unlink(missing_ok=True)
        return path

    def mark_deployed(self, candidate_id: str) -> None:
        path = PROMOTED_DIR / f"{candidate_id}.json"
        if path.exists():
            data = json.loads(path.read_text())
            data["deployed"] = True
            path.write_text(json.dumps(data, indent=2))

    # ─────────────────────────────────────────────────────────────────────────
    # READ OPERATIONS
    # ─────────────────────────────────────────────────────────────────────────

    def list_promoted(self, limit: int = 50) -> list[StrategyCandidate]:
        results = []
        for f in sorted(PROMOTED_DIR.glob("*.json"), reverse=True)[:limit]:
            try:
                results.append(StrategyCandidate(**json.loads(f.read_text())))
            except Exception as e:
                logger.warning("[CandidateStore] Bad file %s: %s", f, e)
        return results

    def get_best_promoted(
        self, strategy_id: str, symbol: str, timeframe: str
    ) -> Optional[StrategyCandidate]:
        """Best (highest score) promoted candidate for a specific pair."""
        matches = [
            c for c in self.list_promoted()
            if c.strategy_id == strategy_id
            and c.symbol == symbol
            and c.timeframe == timeframe
        ]
        return max(matches, key=lambda c: c.score) if matches else None

    def summary(self) -> dict:
        return {
            "pending":  len(list(PENDING_DIR.glob("*.json"))),
            "promoted": len(list(PROMOTED_DIR.glob("*.json"))),
            "rejected": len(list(REJECTED_DIR.glob("*.json"))),
        }
