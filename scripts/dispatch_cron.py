"""Task-Scheduler-friendly Cron trigger for the shared Gateway."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch a scheduled AgentEvent")
    parser.add_argument("job_name")
    parser.add_argument("content", help="normal investment request, e.g. 分析 600519")
    args = parser.parse_args()

    from control_plane.gateway import Gateway
    from control_plane.investment_runtime import InvestmentRuntime
    from control_plane.run_store import PostgresRunStore
    from control_plane.triggers import cron_event

    result = Gateway(InvestmentRuntime(), store=PostgresRunStore()).dispatch(
        cron_event(args.job_name, args.content)
    )
    print({"run_id": result.run_id, "route": result.route, "status": result.payload.get("status")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
