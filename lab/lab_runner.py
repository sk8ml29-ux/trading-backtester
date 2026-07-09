"""
Lab Runner — autonomous strategy optimization loop.

Lifecycle (one iteration):
  1. Load production portfolio as benchmark
  2. For each (symbol, strategy, timeframe) pair:
       a. Fetch latest market data
       b. Evaluate incumbent params on OOS slice → benchmark metrics
       c. Generate N parameter mutations
       d. Evaluate each candidate on OOS slice
       e. Run each through RiskGatekeeper.check_strategy_promotion()
       f. If PASS → backup → promote → optionally update portfolio JSON
  3. Log summary

The lab never touches:
  - live state files (data/live/*.json)
  - the gatekeeper (risk/gatekeeper.py)
  - the core engine or broker

Runs as a daemon thread inside run_cloud.py, or standalone for testing:
  python -m lab.lab_runner
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
PRODUCTION_PORTFOLIOS = [
    ROOT / "mixed_portfolio_oos.json",
    ROOT / "mixed_portfolio_oos_forex.json",
]
LAB_LOG = ROOT / "data" / "live" / "lab_runner.log"


class LabRunner:
    """
    Autonomous parameter optimization loop.
    Thread-safe for daemon use inside run_cloud.py.
    """

    def __init__(
        self,
        portfolio_files: list[Path] | None = None,
        iterations_per_pair: int = 8,
        sleep_between_runs_s: int = 3600 * 6,   # 6 hours default
        auto_promote: bool = True,
    ):
        self.portfolio_files = portfolio_files or PRODUCTION_PORTFOLIOS
        self.iterations_per_pair = iterations_per_pair
        self.sleep_between_runs_s = sleep_between_runs_s
        self.auto_promote = auto_promote

        # Lazy imports — avoid circular deps and heavy loads at module level
        from lab.evaluator import LabEvaluator
        from lab.candidate_store import CandidateStore
        from risk.gatekeeper import RiskGatekeeper
        from state.backup_manager import create_backup

        self.evaluator = LabEvaluator()
        self.store = CandidateStore()
        self.gatekeeper = RiskGatekeeper()
        self._create_backup = create_backup

        self._setup_logging()

    # ─────────────────────────────────────────────────────────────────────────
    # ENTRY POINTS
    # ─────────────────────────────────────────────────────────────────────────

    def run_forever(self) -> None:
        """Infinite loop — call from daemon thread in run_cloud.py."""
        logger.info("[LabRunner] Autonomous optimization loop started")
        while True:
            try:
                self._run_once()
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("[LabRunner] Unhandled error: %s", e, exc_info=True)
            logger.info("[LabRunner] Sleeping %ds until next run", self.sleep_between_runs_s)
            time.sleep(self.sleep_between_runs_s)

    def run_once(self) -> dict[str, Any]:
        """Single lab iteration — suitable for tests and the scheduler."""
        return self._run_once()

    # ─────────────────────────────────────────────────────────────────────────
    # CORE LOOP
    # ─────────────────────────────────────────────────────────────────────────

    def _run_once(self) -> dict[str, Any]:
        started = datetime.utcnow()
        logger.info("[LabRunner] === Run start %s ===", started.isoformat())

        promoted_count = 0
        rejected_count = 0
        promotions: list[dict] = []

        for pf_path in self.portfolio_files:
            pairs = self._load_portfolio(pf_path)
            if not pairs:
                logger.warning("[LabRunner] Empty / missing portfolio: %s", pf_path.name)
                continue

            for pair in pairs:
                result = self._process_pair(pair, pf_path)
                promoted_count += result["promoted"]
                rejected_count += result["rejected"]
                promotions.extend(result.get("promotions", []))

        elapsed = (datetime.utcnow() - started).total_seconds()
        summary = {
            "status": "ok",
            "started": started.isoformat(),
            "elapsed_s": round(elapsed, 1),
            "promoted": promoted_count,
            "rejected": rejected_count,
            "promotions": promotions,
        }
        logger.info(
            "[LabRunner] === Run done: %d promoted, %d rejected in %.1fs ===",
            promoted_count, rejected_count, elapsed,
        )
        return summary

    # ─────────────────────────────────────────────────────────────────────────
    # PER-PAIR OPTIMIZATION
    # ─────────────────────────────────────────────────────────────────────────

    def _process_pair(self, pair: dict, portfolio_path: Path) -> dict:
        symbol = pair.get("symbol") or (pair.get("symbols") or [None])[0]
        strategy_id = pair.get("strategy")
        timeframe = pair.get("timeframe", "30m")
        base_params = pair.get("params", {})

        if not symbol or not strategy_id:
            return {"promoted": 0, "rejected": 0}

        logger.info("[LabRunner] Processing %s / %s / %s", symbol, strategy_id, timeframe)

        df = self._fetch_data(symbol, timeframe)
        if df is None or len(df) < 600:
            logger.warning("[LabRunner] Insufficient data for %s/%s", symbol, timeframe)
            return {"promoted": 0, "rejected": 0}

        # Benchmark: incumbent params on OOS
        incumbent_metrics = self._incumbent_metrics(df, strategy_id, symbol, timeframe, base_params)

        # Generate and test candidates
        from lab.optimizer import generate_candidates
        from lab.candidate_store import StrategyCandidate

        candidates_params = generate_candidates(base_params, n=self.iterations_per_pair)

        promoted = 0
        rejected = 0
        promotions: list[dict] = []

        for params in candidates_params:
            eval_result = self.evaluator.evaluate(strategy_id, symbol, timeframe, params, df)
            if eval_result is None or not eval_result.passed:
                rejected += 1
                continue

            score = self.evaluator.score(eval_result.oos_metrics)
            cid = f"{strategy_id}_{symbol}_{timeframe}_{uuid.uuid4().hex[:8]}"

            candidate = StrategyCandidate(
                candidate_id=cid,
                strategy_id=strategy_id,
                symbol=symbol,
                timeframe=timeframe,
                params=params,
                oos_metrics=eval_result.oos_metrics,
                score=score,
                created_at=datetime.utcnow().isoformat(),
            )
            self.store.save_pending(candidate)

            verdict = self.gatekeeper.check_strategy_promotion(
                candidate_metrics=eval_result.oos_metrics,
                incumbent_metrics=incumbent_metrics,
                oos_days=eval_result.oos_days,
            )

            if verdict.allowed and self.auto_promote:
                self._create_backup(reason=f"pre_promotion_{cid}")
                self.store.promote(candidate, verdict.reason)
                promoted += 1
                promotions.append({
                    "id": cid,
                    "strategy": strategy_id,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "score": score,
                    "verdict": verdict.reason,
                    "metrics": eval_result.oos_metrics,
                })
                logger.info("[LabRunner] PROMOTED %s: %s", cid, verdict.reason)

                # Update portfolio JSON if this candidate is significantly better
                if self._should_replace_incumbent(incumbent_metrics, eval_result.oos_metrics):
                    self._update_portfolio(portfolio_path, pair, params, eval_result.oos_metrics)
            else:
                self.store.reject(candidate, verdict.reason)
                rejected += 1
                logger.debug("[LabRunner] rejected %s: %s", cid, verdict.reason)

        return {"promoted": promoted, "rejected": rejected, "promotions": promotions}

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _load_portfolio(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text())
            # Portfolio files are dicts with a "pairs" key, not bare lists.
            if isinstance(data, dict):
                return data.get("pairs", [])
            return data  # bare list (legacy format)
        except Exception as e:
            logger.error("[LabRunner] Cannot parse %s: %s", path, e)
            return []

    def _fetch_data(self, symbol: str, timeframe: str):
        try:
            from backtest.data_loader import fetch_ohlcv
            return fetch_ohlcv(symbol, timeframe)
        except Exception as e:
            logger.error("[LabRunner] Data fetch failed %s/%s: %s", symbol, timeframe, e)
            return None

    def _incumbent_metrics(
        self, df, strategy_id: str, symbol: str, timeframe: str, params: dict
    ) -> dict:
        """Run incumbent params on OOS to get baseline metrics."""
        try:
            r = self.evaluator.evaluate(strategy_id, symbol, timeframe, params, df)
            if r and r.passed:
                return r.oos_metrics
        except Exception as e:
            logger.warning("[LabRunner] Incumbent eval error: %s", e)
        # Fallback — conservative floor so any real candidate needs to beat it
        return {"profit_factor": 1.10, "sharpe": 0.50, "max_drawdown": 0.15, "n_trades": 15}

    def _should_replace_incumbent(self, incumbent: dict, candidate: dict) -> bool:
        """True if candidate score is ≥10 % better than incumbent."""
        from risk.gatekeeper import PROMOTION_IMPROVEMENT_THRESHOLD
        inc = self.evaluator.score(incumbent)
        cnd = self.evaluator.score(candidate)
        if inc <= 0:
            return cnd > 0
        return (cnd - inc) / abs(inc) >= PROMOTION_IMPROVEMENT_THRESHOLD

    def _update_portfolio(
        self, path: Path, pair: dict, new_params: dict, metrics: dict
    ) -> None:
        """Replace params for one pair in the portfolio JSON (after backup)."""
        try:
            portfolio = self._load_portfolio(path)
            symbol = pair.get("symbol") or (pair.get("symbols") or [None])[0]
            strategy_id = pair.get("strategy")
            timeframe = pair.get("timeframe")

            for p in portfolio:
                p_sym = p.get("symbol") or (p.get("symbols") or [None])[0]
                if (
                    p_sym == symbol
                    and p.get("strategy") == strategy_id
                    and p.get("timeframe") == timeframe
                ):
                    p["params"] = new_params
                    p["last_optimized"] = datetime.utcnow().isoformat()
                    p["last_oos_metrics"] = metrics
                    break

            path.write_text(json.dumps(portfolio, indent=2))
            logger.info("[LabRunner] Portfolio updated: %s/%s/%s in %s", symbol, strategy_id, timeframe, path.name)
        except Exception as e:
            logger.error("[LabRunner] Portfolio update failed: %s", e, exc_info=True)

    def _setup_logging(self) -> None:
        LAB_LOG.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(LAB_LOG), mode="a", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s"))
        logging.getLogger("lab").addHandler(fh)
        logging.getLogger("lab").setLevel(logging.INFO)


# ─────────────────────────────────────────────────────────────────────────────
# CLI shim: python -m lab.lab_runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    runner = LabRunner(iterations_per_pair=5, auto_promote=True)
    result = runner.run_once()
    print(json.dumps(result, indent=2))
