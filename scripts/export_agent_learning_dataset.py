#!/usr/bin/env python3
"""Export human-approved Agent SFT/DPO records from PostgreSQL to JSONL.

Usage:
    python scripts/export_agent_learning_dataset.py --kind sft --out data/agent_sft.jsonl
    python scripts/export_agent_learning_dataset.py --kind dpo --out data/agent_dpo.jsonl
    python scripts/export_agent_learning_dataset.py --kind sft --format llamafactory-alpaca --out data/agent_sft.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_learning.dataset_export import candidate_to_training_row


def load_approved_candidates(kind: str) -> list[dict[str, Any]]:
    from db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT candidate_id, run_id, candidate_type, status, sample, reviewer
                FROM agent_training_candidates
                WHERE status = 'approved' AND candidate_type = %s
                ORDER BY reviewed_at NULLS LAST, created_at
                """,
                (kind,),
            )
            rows = cur.fetchall()
    return [
        {
            "candidate_id": row[0],
            "run_id": row[1],
            "candidate_type": row[2],
            "status": row[3],
            "sample": row[4],
            "reviewer": row[5],
        }
        for row in rows
    ]


def mark_exported(candidate_ids: list[str]) -> None:
    if not candidate_ids:
        return
    from db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agent_training_candidates
                SET status = 'exported', exported_at = NOW()
                WHERE candidate_id = ANY(%s) AND status = 'approved'
                """,
                (candidate_ids,),
            )
        conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Export approved Agent post-training candidates")
    parser.add_argument("--kind", choices=("sft", "dpo"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--format", choices=("generic-jsonl", "llamafactory-alpaca"), default="generic-jsonl")
    parser.add_argument("--mark-exported", action="store_true")
    args = parser.parse_args()

    rows = load_approved_candidates(args.kind)
    rendered: list[dict[str, Any]] = []
    exported_ids: list[str] = []
    for candidate in rows:
        try:
            rendered.append(candidate_to_training_row(candidate, output_format=args.format))
            exported_ids.append(str(candidate["candidate_id"]))
        except ValueError as exc:
            print(f"skip {candidate['candidate_id']}: {exc}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        if args.format == "llamafactory-alpaca":
            json.dump(rendered, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        else:
            for row in rendered:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    if args.mark_exported:
        mark_exported(exported_ids)
    print(f"exported {len(rendered)} {args.kind} samples to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
