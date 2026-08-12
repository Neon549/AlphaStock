"""Generate a frozen synthetic robustness set from reviewed candidate facts.

The variants stress aliases, stock codes, colloquial wording and filing anchors.
They are kept in a separate tier because template-generated traffic must never be
presented as real user distribution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from evaluation.download_corpus import DEFAULT_SOURCE_MANIFEST, load_sources
from evaluation.rag_golden_eval import load_cases
from evaluation.run_candidate_rag_eval import DEFAULT_CASES


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "evaluation" / "corpus" / "production_candidate_v1" / "rag_query_variants.jsonl"
DEFAULT_SNAPSHOT_OUT = ROOT / "evaluation" / "corpus" / "production_candidate_v1" / "QUERY_VARIANTS_SNAPSHOT.json"
FACT_LABELS = {
    "revenue": "营业收入",
    "operating_cash_flow": "经营活动产生的现金流量净额",
    "net_profit_attributable": "归属于上市公司股东的净利润",
    "non_performing_loan_ratio": "不良贷款率",
    "net_interest_margin": "净息差",
}


def _source_for_case(case: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    citations = case["expected"].get("required_citations", [])
    if citations:
        document_id = str(citations[0]["filename"]).removesuffix(".pdf")
        return next(source for source in sources if source["document_id"] == document_id)
    matches = [source for source in sources if str(source["company"]) in str(case["query"])]
    if not matches:
        raise ValueError(f"{case['id']}: cannot resolve source company")
    return max(matches, key=lambda item: str(item["report_period"]))


def _metric_label(case: dict[str, Any]) -> str:
    facts = case["expected"].get("answer_facts", [])
    if facts:
        fact_name = str(facts[0].get("name", ""))
        if fact_name not in FACT_LABELS:
            raise ValueError(f"{case['id']}: no robustness label for {fact_name}")
        return FACT_LABELS[fact_name]
    for label in FACT_LABELS.values():
        if label in str(case["query"]):
            return label
    raise ValueError(f"{case['id']}: cannot resolve metric label")


def _requested_period(case: dict[str, Any], source: dict[str, Any]) -> tuple[str, bool]:
    query = str(case["query"])
    year_match = re.search(r"20\d{2}", query)
    year = year_match.group(0) if year_match else str(source["report_period"]).replace("FY", "")
    quarterly = any(marker in query for marker in ("第一季度", "一季度", "Q1", "q1"))
    return year, quarterly


def build_variants(cases: list[dict[str, Any]], sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        source = _source_for_case(case, sources)
        company = str(source["company"])
        code = str(source["security_code"])
        metric = _metric_label(case)
        year, quarterly = _requested_period(case, source)
        period_short = f"{year}Q1" if quarterly else year
        period_cn = f"{year}年一季度" if quarterly else f"{year}年"
        period_filing = f"{year}年第一季度报告" if quarterly else f"{year}年报"
        variants = {
            "stock_code_short": f"{code} {period_short} {metric}",
            "colloquial": f"帮我看看{company}{period_cn}的{metric}",
            "filing_anchored": f"{company}{period_filing}披露的{metric}是多少？",
            "mixed_identifier": f"{company} / {code}，{period_cn} {metric} 数据",
        }
        for variant_type, query in variants.items():
            row = deepcopy(case)
            row["id"] = f"{case['id']}--{variant_type}"
            row["query"] = query
            row["parent_case_id"] = case["id"]
            row["variant_type"] = variant_type
            row["tags"] = [*case.get("tags", []), "synthetic_query_robustness", variant_type]
            row["provenance"] = {
                "origin": "deterministic_template_from_public_filing_candidate",
                "reviewer": "template_generated_pending_human_review",
                "reviewed_at": "",
            }
            rows.append(row)
    return rows


def write_dataset(rows: Iterable[dict[str, Any]], out: Path, snapshot_out: Path) -> dict[str, Any]:
    serialised = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(serialised.encode("utf-8"))
    snapshot = {
        "dataset_id": "public-filings-query-robustness-candidate-v1",
        "tier": "synthetic_candidate_pending_human_review",
        "row_count": len(serialised.splitlines()),
        "sha256": hashlib.sha256(serialised.encode("utf-8")).hexdigest(),
        "generator": "evaluation.build_rag_query_variants",
        "warning": "Template-generated robustness traffic is not a real-user test distribution or resume metric.",
    }
    snapshot_out.write_bytes((json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Build frozen RAG query robustness variants")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--snapshot-out", type=Path, default=DEFAULT_SNAPSHOT_OUT)
    args = parser.parse_args()
    rows = build_variants(load_cases(args.cases), load_sources(args.sources)["documents"])
    print(json.dumps(write_dataset(rows, args.out, args.snapshot_out), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
