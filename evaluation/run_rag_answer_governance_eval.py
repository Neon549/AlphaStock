"""Run deterministic RAG answer-governance checks against a fixed fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.rag_golden_eval import evaluate_answer_governance, load_cases


DEFAULT_ANSWERS = ROOT / "evaluation" / "fixtures" / "rag_answer_governance_v1.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate RAG citation and abstention governance")
    parser.add_argument("--answers-file", type=Path, default=DEFAULT_ANSWERS)
    args = parser.parse_args(argv)
    answers = json.loads(args.answers_file.read_text(encoding="utf-8"))
    if not isinstance(answers, dict):
        raise ValueError("answers fixture must be a JSON object keyed by Golden Set case id")
    print(json.dumps(evaluate_answer_governance(load_cases(), answers), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
