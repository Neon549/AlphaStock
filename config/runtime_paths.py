"""Filesystem locations for disposable runtime state, outside source directories."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
CACHE_DIR = RUNTIME_DIR / "cache"
REPORTS_DIR = RUNTIME_DIR / "reports"
TMP_DIR = RUNTIME_DIR / "tmp"
CHECKPOINT_DB_PATH = RUNTIME_DIR / "checkpoints.db"
STOCK_UNIVERSE_CACHE_FILE = CACHE_DIR / "stock_universe_cache.json"


def ensure_runtime_dirs() -> None:
    """Create only disposable directories; source and persistence paths stay untouched."""
    for path in (CACHE_DIR, REPORTS_DIR, TMP_DIR):
        path.mkdir(parents=True, exist_ok=True)
