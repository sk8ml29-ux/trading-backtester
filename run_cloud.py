"""
Cloud Master Orchestrator
=========================
Runs three daemon threads in parallel:

  backup   — full snapshot every hour (configurable)
  lab      — autonomous parameter optimization every 6 hours
  health   — portfolio health pulse every 5 minutes via RiskGatekeeper

Safe shutdown on SIGINT / SIGTERM:  all threads exit cleanly.

Usage
-----
  python run_cloud.py                          # start all daemons
  python run_cloud.py --lab-only               # single lab run, then exit
  python run_cloud.py --backup-only            # single backup, then exit
  python run_cloud.py --health                 # print health report, then exit
  python run_cloud.py --lab-interval 7200      # custom lab interval (seconds)
  python run_cloud.py --backup-interval 1800   # custom backup interval (seconds)

systemd  →  see deploy/trading-cloud.service
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
LOG_DIR = ROOT / "data" / "live"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-16s %(levelname)-8s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_DIR / "cloud.log"), mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("cloud")

_SHUTDOWN = threading.Event()


def _handle_signal(sig, _frame):
    logger.warning("[Cloud] Signal %s — shutting down", sig)
    _SHUTDOWN.set()


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ─────────────────────────────────────────────────────────────────────────────
# DAEMON THREADS
# ─────────────────────────────────────────────────────────────────────────────

def _backup_loop(interval_s: int) -> None:
    """Periodic backup — runs until _SHUTDOWN is set."""
    from state.backup_manager import create_backup
    logger.info("[BackupLoop] Starting — interval %ds", interval_s)
    while not _SHUTDOWN.wait(interval_s):
        try:
            path = create_backup(reason="scheduled_hourly")
            logger.info("[BackupLoop] Snapshot → %s", path)
        except Exception as e:
            logger.error("[BackupLoop] Error: %s", e, exc_info=True)


def _lab_loop(interval_s: int) -> None:
    """Autonomous optimization loop — runs until _SHUTDOWN is set."""
    from lab.lab_runner import LabRunner
    runner = LabRunner(sleep_between_runs_s=interval_s)
    logger.info("[LabLoop] Starting — interval %ds", interval_s)
    while not _SHUTDOWN.is_set():
        try:
            summary = runner.run_once()
            logger.info(
                "[LabLoop] Complete — promoted=%d rejected=%d elapsed=%.0fs",
                summary.get("promoted", 0),
                summary.get("rejected", 0),
                summary.get("elapsed_s", 0),
            )
        except Exception as e:
            logger.error("[LabLoop] Error: %s", e, exc_info=True)
        _SHUTDOWN.wait(interval_s)


def _health_loop(interval_s: int = 300) -> None:
    """
    Portfolio health pulse — checks drawdown / daily loss every interval_s.
    Logs WARN and HALT verdicts from the Gatekeeper.
    """
    from state.state_manager import StateManager
    from risk.gatekeeper import RiskGatekeeper

    sm = StateManager()
    gk = RiskGatekeeper()
    logger.info("[HealthLoop] Starting — interval %ds", interval_s)

    while not _SHUTDOWN.wait(interval_s):
        try:
            eq_map = sm.equity_map()
            peak_map = sm.peak_equity_map()
            daily_map = sm.daily_start_equity_map()
            positions = sm.open_positions()

            total_eq = sum(eq_map.values())
            total_peak = sum(peak_map.values())
            total_daily = sum(daily_map.values())

            if total_eq > 0:
                verdict = gk.check_portfolio_health(total_eq, total_peak, total_daily)
                if verdict.severity == "halt":
                    logger.critical("[HealthLoop] %s — equity=%.2f peak=%.2f", verdict.reason, total_eq, total_peak)
                elif verdict.severity == "warn":
                    logger.warning("[HealthLoop] %s", verdict.reason)
                else:
                    logger.info(
                        "[HealthLoop] OK — bots=%d positions=%d equity=%.2f",
                        len(eq_map), len(positions), total_eq,
                    )

        except Exception as e:
            logger.error("[HealthLoop] Error: %s", e, exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE-SHOT COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

def cmd_backup() -> None:
    from state.backup_manager import create_backup
    path = create_backup(reason="manual_cli")
    print(f"Backup created: {path}")


def cmd_lab_once() -> None:
    from lab.lab_runner import LabRunner
    runner = LabRunner()
    summary = runner.run_once()
    print(json.dumps(summary, indent=2))


def cmd_health() -> None:
    from state.state_manager import StateManager
    from state.backup_manager import get_latest_stable, list_backups
    from lab.candidate_store import CandidateStore

    sm = StateManager()
    cs = CandidateStore()

    snap = sm.snapshot()
    eq = sm.equity_map()
    positions = sm.open_positions()
    latest_backup = get_latest_stable()

    print(f"\n{'─'*60}")
    print(f"  Cloud Health Report   {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'─'*60}")
    print(f"  Active bots      : {snap['total_bots']}")
    print(f"  Open positions   : {len(positions)}")
    if eq:
        print(f"  Total equity     : {sum(eq.values()):.2f}")
        for bot_id, e in eq.items():
            print(f"    {bot_id:<40} {e:.2f}")
    print(f"\n  Latest backup    : {latest_backup or 'none'}")
    print(f"\n  Candidate store  : {cs.summary()}")
    print(f"\n  Recent backups:")
    for b in list_backups(5):
        print(f"    {b['timestamp']}  {b['reason']}")
    print(f"{'─'*60}\n")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cloud orchestrator — backup + lab + health monitor"
    )
    parser.add_argument("--lab-only",        action="store_true", help="Run lab once and exit")
    parser.add_argument("--backup-only",     action="store_true", help="Create backup and exit")
    parser.add_argument("--health",          action="store_true", help="Print health report and exit")
    parser.add_argument("--lab-interval",    type=int, default=21600, metavar="S",
                        help="Lab run interval in seconds (default 21600 = 6h)")
    parser.add_argument("--backup-interval", type=int, default=3600,  metavar="S",
                        help="Backup interval in seconds (default 3600 = 1h)")
    args = parser.parse_args()

    if args.backup_only:
        cmd_backup()
        return

    if args.lab_only:
        cmd_lab_once()
        return

    if args.health:
        cmd_health()
        return

    # ── Daemon mode ──────────────────────────────────────────────────────────

    # Initial backup before starting
    try:
        from state.backup_manager import create_backup
        create_backup(reason="startup")
        logger.info("[Cloud] Startup backup complete")
    except Exception as e:
        logger.warning("[Cloud] Startup backup failed: %s", e)

    threads = [
        threading.Thread(
            target=_backup_loop,
            args=(args.backup_interval,),
            daemon=True,
            name="backup",
        ),
        threading.Thread(
            target=_lab_loop,
            args=(args.lab_interval,),
            daemon=True,
            name="lab",
        ),
        threading.Thread(
            target=_health_loop,
            args=(300,),
            daemon=True,
            name="health",
        ),
    ]

    for t in threads:
        t.start()
        logger.info("[Cloud] Thread '%s' started", t.name)

    logger.info("[Cloud] All systems running.  SIGINT or SIGTERM to stop.")

    try:
        while not _SHUTDOWN.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        _SHUTDOWN.set()

    logger.info("[Cloud] Shutdown complete")


if __name__ == "__main__":
    main()
