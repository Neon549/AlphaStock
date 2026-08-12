"""Run a reportable final RAG retrieval evaluation only after admission.

Unlike the candidate runner, this entrypoint first enforces real-query
provenance, PII scanning and no-overlap with retriever-selection datasets.
It intentionally starts with deterministic BM25 so the first final baseline
does not depend on an LLM or a locally cached embedding model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.frozen_dataset import load_jsonl
from evaluation.rag_golden_eval import evaluate_retrieval_cases
from evaluation.real_rag_test_admission import DEFAULT_REFERENCE_DATASETS, audit_final_test_rows
from evaluation.run_candidate_rag_eval import (
    build_scoped_bm25_retriever,
    load_candidate_corpus,
    validate_label_integrity,
)
from evaluation.rag_snapshot_retrievers import build_bm25_retriever


ROOT = Path(__file__).resolve().parent.parent


def run_final_evaluation(
    cases_path: Path,
    chunks_path: Path,
    source_manifest: Path,
    reference_paths: list[Path],
    *,
    k: int = 10,
) -> dict[str, Any]:
    cases = load_jsonl(cases_path)
    reference_rows = [row for path in reference_paths for row in load_jsonl(path)]
    admission = audit_final_test_rows(cases, reference_rows)
    if not admission["valid"]:
        raise ValueError("final RAG test admission failed: " + "; ".join(admission["errors"]))
    corpus = load_candidate_corpus(chunks_path, source_manifest)
    label_integrity = validate_label_integrity(cases, corpus)
    if not label_integrity["valid"]:
        raise ValueError("final RAG label integrity failed: " + json.dumps(label_integrity["errors"], ensure_ascii=False))
    retrievers = {
        "bm25_global": build_bm25_retriever(corpus),
        "bm25_entity_period_scoped": build_scoped_bm25_retriever(corpus),
    }
    return {
        "dataset_tier": "production_final_test",
        "claim_boundary": "Frozen, independently reviewed real-query test; report together with corpus, admission report and confidence intervals.",
        "cases": len(cases),
        "corpus_chunks": len(corpus),
        "k": k,
        "admission": admission,
        "label_integrity": label_integrity,
        "results": {name: evaluate_retrieval_cases(cases, retriever, k=k) for name, retriever in retrievers.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run admitted AlphaStock final RAG retrieval evaluation")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--reference", type=Path, action="append", default=[])
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run_final_evaluation(
            args.cases,
            args.chunks,
            args.source_manifest,
            args.reference or list(DEFAULT_REFERENCE_DATASETS),
            k=args.k,
        )
    except ValueError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
