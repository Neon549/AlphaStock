"""Select a deterministic, page-anchored CFQA batch for source mapping."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any


def flatten_pages(value: Any) -> list[int]:
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        pages: list[int] = []
        for item in value:
            pages.extend(flatten_pages(item))
        return sorted(set(pages))
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260813)
    args = parser.parse_args()

    rows = json.loads(args.source.read_text(encoding="utf-8"))
    usable = [
        row for row in rows
        if str(row.get("问题", "")).strip()
        and str(row.get("答案", "")).strip()
        and flatten_pages(row.get("答案出自"))
    ]
    if args.count > len(usable):
        raise SystemExit(f"requested {args.count}, only {len(usable)} page-anchored rows available")
    selected = random.Random(args.seed).sample(usable, args.count)
    output_rows = []
    for index, row in enumerate(selected, start=1):
        output_rows.append(
            {
                "id": f"cfqa-v1-{index:03d}",
                "query": row["问题"].strip(),
                "reference_answer": row["答案"].strip(),
                "stock_code": str(row.get("股票代码", "")).strip(),
                "company": str(row.get("公司", "")).strip(),
                "answer_pdf_pages": flatten_pages(row.get("答案出自")),
                "cfqa_id": row.get("id"),
                "status": "source_resolution_pending",
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in output_rows),
        encoding="utf-8",
    )
    print(json.dumps({"usable_page_anchored_rows": len(usable), "selected": len(output_rows), "seed": args.seed, "output": str(args.output)}, ensure_ascii=False))
    for row in output_rows:
        print(row["id"], row["stock_code"], row["company"], row["answer_pdf_pages"], row["query"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
