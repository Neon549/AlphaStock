"""Create a frozen synthetic stress set for deterministic query rewrite.

It derives only from the pinned candidate labels and is intentionally tagged
synthetic.  It exists to expose whether aliases and colloquial finance wording
recover evidence that the existing entity/period baseline cannot scope.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from evaluation.rag_golden_eval import load_cases


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "evaluation" / "corpus" / "production_candidate_v1" / "rag_candidates.jsonl"
DEFAULT_OUT = ROOT / "evaluation" / "corpus" / "production_candidate_v1" / "rag_query_rewrite_stress_candidate_v1.jsonl"
ALIASES = {
    "600519": "茅台", "300750": "宁德", "000001": "平银", "601012": "隆基",
    "002415": "海康", "002714": "牧原", "000333": "美的",
}
FACT_WORDING = {
    "revenue": "营收", "operating_cash_flow": "经营现金流", "net_profit_attributable": "利润",
    "non_performing_loan_ratio": "坏账率", "net_interest_margin": "息差",
}


def build_rows(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        facts = case.get("expected", {}).get("answer_facts", [])
        citations = case.get("expected", {}).get("required_citations", [])
        if not facts or not citations:
            continue
        fact_name = str(facts[0].get("name", ""))
        filename = str(citations[0].get("filename", ""))
        code_match = re.match(r"([036]\d{5}|688\d{3})-", filename)
        if not code_match or code_match.group(1) not in ALIASES or fact_name not in FACT_WORDING:
            continue
        code = code_match.group(1)
        year_match = re.search(r"20\d{2}", filename)
        if not year_match:
            continue
        row = deepcopy(case)
        row["id"] = f"{case['id']}--alias-colloquial"
        row["parent_case_id"] = case["id"]
        row["query"] = f"{ALIASES[code]} {year_match.group(0)} 年 {FACT_WORDING[fact_name]}"
        row["tags"] = [*case.get("tags", []), "synthetic_query_rewrite_stress", "alias", "colloquial"]
        row["provenance"] = {
            "origin": "deterministic_template_from_public_filing_candidate",
            "reviewer": "template_generated_pending_independent_review",
            "reviewed_at": "",
        }
        rows.append(row)
    return rows


def write_rows(rows: list[dict[str, Any]], out: Path) -> dict[str, Any]:
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Write explicit LF bytes so the recorded hash is reproducible on Windows
    # and Linux alike.
    out.write_bytes(content.encode("utf-8"))
    return {"cases": len(rows), "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(), "out": str(out)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a synthetic alias/colloquial query rewrite stress set")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(json.dumps(write_rows(build_rows(load_cases(args.cases)), args.out), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
