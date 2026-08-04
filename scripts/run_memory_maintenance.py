"""Scheduled worker entrypoint for pending memory extraction and sleep reports."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated Agent-memory maintenance")
    parser.add_argument("--mode", choices=("extract", "sleep", "all"), default="all")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    from agent_runtime.memory.maintenance import run_one_extraction_job, write_sleep_consolidation_report

    if args.mode in {"extract", "all"}:
        for _ in range(max(1, min(args.limit, 20))):
            result = run_one_extraction_job()
            if result is None:
                break
            print(result)
    if args.mode in {"sleep", "all"}:
        print({"sleep_report": str(write_sleep_consolidation_report())})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
