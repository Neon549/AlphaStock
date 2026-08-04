"""Manually index approved Agent-memory Markdown into PostgreSQL + pgvector."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Running ``python scripts/sync_memory_index.py`` puts ``scripts/`` rather
# than the project root on sys.path. Keep this CLI independently runnable.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_runtime.memory.index import MEMORY_KNOWLEDGE_DIR, sync_memory_index


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync approved Agent-memory Markdown")
    parser.add_argument("--root", type=Path, default=MEMORY_KNOWLEDGE_DIR)
    parser.add_argument("--keep-stale", action="store_true", help="do not remove entries for deleted files")
    args = parser.parse_args()
    print(json.dumps(sync_memory_index(args.root, prune=not args.keep_stale), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
