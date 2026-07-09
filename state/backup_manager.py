"""
Backup Manager — full system snapshots + rollback on crash.

Creates timestamped backups of:
  - All live state JSONs  (data/live/*.json)
  - Production portfolio configs  (*.json root)
  - Core source modules  (backtest/, strategies/, live/, risk/)

On rollback: restores state + configs but NOT source code, so the
Gatekeeper and core engine can never be silently reverted by the lab.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
BACKUP_ROOT = ROOT / "backups"
LIVE_DATA = ROOT / "data" / "live"

# Root-level portfolio / config JSONs that must be snapshotted
PORTFOLIO_CONFIGS = [
    "mixed_portfolio_oos.json",
    "mixed_portfolio_oos_forex.json",
    "spread_config.json",
    "optimized_30m.json",
    "optimized_30m_by_symbol.json",
]

# Source modules included in the code snapshot (for audit trail only)
CODE_MODULES = [
    "backtest",
    "strategies",
    "live",
    "risk",
    "config.py",
    "run_live.py",
    "run_backtest.py",
]

_METADATA_FILE = "backup_metadata.json"
_LATEST_STABLE_PTR = BACKUP_ROOT / "latest_stable.json"


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _checksum(path: Path) -> str:
    """SHA-256 (first 16 hex chars) of file or directory tree."""
    h = hashlib.sha256()
    if path.is_file():
        h.update(path.read_bytes())
    elif path.is_dir():
        for f in sorted(path.rglob("*")):
            if f.is_file():
                h.update(f.read_bytes())
    return h.hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def create_backup(reason: str = "scheduled") -> Path:
    """
    Create a full system snapshot.  Returns the backup directory Path.
    Safe to call at any time — does not interrupt live trading.
    """
    ts = datetime.utcnow().strftime("%Y-%m-%d/%H-%M-%S")
    backup_dir = BACKUP_ROOT / ts
    backup_dir.mkdir(parents=True, exist_ok=True)

    metadata: dict = {
        "timestamp": datetime.utcnow().isoformat(),
        "reason": reason,
        "files": {},
    }

    # 1. Live state JSONs
    live_dst = backup_dir / "data" / "live"
    live_dst.mkdir(parents=True, exist_ok=True)
    if LIVE_DATA.exists():
        for f in LIVE_DATA.glob("*.json"):
            shutil.copy2(f, live_dst / f.name)
            metadata["files"][f"data/live/{f.name}"] = _checksum(f)

    # 2. Portfolio / config JSONs
    for cfg_name in PORTFOLIO_CONFIGS:
        src = ROOT / cfg_name
        if src.exists():
            shutil.copy2(src, backup_dir / cfg_name)
            metadata["files"][cfg_name] = _checksum(src)

    # 3. Core source modules (audit trail — rollback does NOT restore these)
    code_dst = backup_dir / "code"
    code_dst.mkdir(parents=True, exist_ok=True)
    for mod in CODE_MODULES:
        src = ROOT / mod
        if src.exists():
            dst = code_dst / mod
            if src.is_dir():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(src, dst)
            metadata["files"][f"code/{mod}"] = _checksum(src)

    # 4. Persist metadata
    (backup_dir / _METADATA_FILE).write_text(json.dumps(metadata, indent=2))

    # 5. Update latest-stable pointer
    _LATEST_STABLE_PTR.write_text(
        json.dumps(
            {
                "path": str(backup_dir),
                "timestamp": metadata["timestamp"],
                "reason": reason,
            },
            indent=2,
        )
    )

    logger.info("[BackupManager] Snapshot: %s  reason=%s", backup_dir, reason)
    return backup_dir


def get_latest_stable() -> Optional[Path]:
    """Return Path to latest stable backup, or None if none exists."""
    if not _LATEST_STABLE_PTR.exists():
        return None
    try:
        data = json.loads(_LATEST_STABLE_PTR.read_text())
        p = Path(data["path"])
        return p if p.exists() else None
    except Exception:
        return None


def rollback(backup_dir: Optional[Path] = None) -> bool:
    """
    Restore live state + portfolio configs from backup.
    Does NOT restore source code — the Gatekeeper stays intact.
    Returns True on success, False on failure.
    """
    target = backup_dir or get_latest_stable()
    if not target:
        logger.error("[BackupManager] No backup available for rollback")
        return False

    meta_path = target / _METADATA_FILE
    if not meta_path.exists():
        logger.error("[BackupManager] Metadata missing in %s", target)
        return False

    try:
        # Restore live state
        live_src = target / "data" / "live"
        if live_src.exists():
            LIVE_DATA.mkdir(parents=True, exist_ok=True)
            for f in live_src.glob("*.json"):
                shutil.copy2(f, LIVE_DATA / f.name)
            logger.warning("[BackupManager] Live state restored from %s", target)

        # Restore portfolio configs
        for cfg_name in PORTFOLIO_CONFIGS:
            src = target / cfg_name
            if src.exists():
                shutil.copy2(src, ROOT / cfg_name)
                logger.warning("[BackupManager] Config restored: %s", cfg_name)

        logger.warning("[BackupManager] Rollback complete — source code unchanged")
        return True

    except Exception as e:
        logger.critical("[BackupManager] Rollback FAILED: %s", e)
        traceback.print_exc()
        return False


def list_backups(limit: int = 10) -> list[dict]:
    """Return recent backups sorted newest-first."""
    results: list[dict] = []
    for meta_file in sorted(BACKUP_ROOT.rglob(_METADATA_FILE), reverse=True)[:limit]:
        try:
            data = json.loads(meta_file.read_text())
            data["path"] = str(meta_file.parent)
            results.append(data)
        except Exception:
            pass
    return results
