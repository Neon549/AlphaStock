"""Build one traceable report for AlphaStock's deterministic evaluation tier.

It intentionally reports current fixture results as regression checks.  It
does not upgrade them into production-quality claims, and it keeps the live
intent parser opt-in because its LLM fallback is not deterministic.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.dataset_manifest import verify_manifest
from evaluation.rag_golden_eval import evaluate_answer_governance, evaluate_retrieval_cases, load_cases
from evaluation.rag_snapshot_retrievers import build_bm25_retriever, load_snapshot
from evaluation.regression_runner import run as run_workflow_regression
from evaluation.run_rag_answer_governance_eval import DEFAULT_ANSWERS


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INTENT_DATASET = ROOT / "data" / "intent" / "eval_smoke.jsonl"


def build_report(*, include_routing: bool = False) -> dict[str, Any]:
    """Run deterministic checks and attach an explicit claim boundary."""

    manifest = verify_manifest()
    corpus = load_snapshot()
    cases = load_cases()
    retrieval = evaluate_retrieval_cases(cases, build_bm25_retriever(corpus), k=3)
    answers = json.loads(DEFAULT_ANSWERS.read_text(encoding="utf-8"))
    answer_governance = evaluate_answer_governance(cases, answers)
    workflow_cases, workflow_failures = run_workflow_regression()

    report: dict[str, Any] = {
        "report_type": "offline_regression",
        "claim_boundary": (
            "All scores in this report come from contract/smoke fixtures. "
            "They are regression signals, not production quality or resume-grade metrics."
        ),
        "manifest": manifest,
        "rag": {
            "corpus_version": "sha256:fixture-rag-corpus-v1",
            "retrieval": {"method": "bm25_only", "metrics": retrieval},
            "answer_governance": answer_governance,
        },
        "workflow_governance": {
            "cases": workflow_cases,
            "passed": not workflow_failures,
            "failures": workflow_failures,
        },
        "routing": {
            "dataset": str(DEFAULT_INTENT_DATASET.relative_to(ROOT)),
            "executed": False,
            "reason": "Routing invokes a layered parser with optional LLM fallback; run explicitly to avoid treating it as deterministic regression.",
        },
    }
    if include_routing:
        from scripts.evaluate_intent_routing import _read_jsonl, evaluate_rows

        report["routing"] = {
            "dataset": str(DEFAULT_INTENT_DATASET.relative_to(ROOT)),
            "executed": True,
            "result": evaluate_rows(_read_jsonl(DEFAULT_INTENT_DATASET)),
            "claim_boundary": "Seeded smoke coverage only; not representative of online routing quality.",
        }
    report["passed"] = bool(manifest["valid"] and not workflow_failures)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a versioned offline AlphaStock evaluation report")
    parser.add_argument("--include-routing", action="store_true", help="Also run the non-deterministic layered intent parser.")
    parser.add_argument("--out", type=Path, help="Optional JSON report output path")
    args = parser.parse_args()
    report = build_report(include_routing=args.include_routing)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
