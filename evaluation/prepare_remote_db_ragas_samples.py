"""Generate answer samples from a remote-db retrieval ablation report."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from evaluation.evaluator import EVAL_DATASET


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RETRIEVAL = ROOT / "runtime" / "reports" / "remote-db-retrieval-ablation.json"
DEFAULT_OUT = ROOT / "runtime" / "reports" / "remote-db-ragas-samples.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrieval", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument(
        "--methods",
        nargs="+",
        default=[
            "bm25_global_expanded",
            "bm25_scoped_expanded",
            "hybrid_scoped",
            "hybrid_global_reranked",
        ],
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=True)
    report = json.loads(args.retrieval.read_text(encoding="utf-8"))
    generator = ChatOpenAI(
        model="deepseek-chat",
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_api_base=os.getenv("OPENAI_API_BASE"),
        temperature=0,
        request_timeout=60,
        max_retries=0,
    )
    by_question = {case["question"]: case for case in EVAL_DATASET}
    output: dict[str, list[dict[str, object]]] = {}
    for method in args.methods:
        rows = report["ragas_samples"][method]
        samples = []
        for index, row in enumerate(rows, 1):
            question = str(row["question"])
            contexts = [str(value) for value in row["contexts"]]
            prompt = (
                "你是一个严谨的中文股票新闻问答助手。只能根据给定证据回答；"
                "证据不足时明确说明无法判断，不要补充证据之外的事实。\n"
                f"问题：{question}\n"
                "证据：\n"
                + "\n".join(f"- {context}" for context in contexts)
            )
            response = generator.invoke(prompt)
            answer = getattr(response, "content", str(response))
            samples.append(
                {
                    "question": question,
                    "answer": answer,
                    "contexts": contexts,
                    "ground_truth": by_question[question]["ground_truth"],
                }
            )
            print(f"{method} generated={index}/{len(rows)}", flush=True)
        output[method] = samples
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"out={args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
