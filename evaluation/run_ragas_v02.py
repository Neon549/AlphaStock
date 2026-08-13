"""Run RAGAS 0.2 evaluation against generated AlphaStock answer samples.

Keep this runner separate from the legacy RAGAS 0.1 adapter: Python 3.13 does
not have wheels for the old NumPy dependency chain.  It is intended to run in
the ignored ``runtime/ragas-venv`` evaluation environment so production
LangChain dependencies stay untouched.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from statistics import mean
from typing import Any

import requests
from dotenv import load_dotenv
from langchain_core.embeddings import Embeddings
from langchain_openai import ChatOpenAI
from ragas import EvaluationDataset, evaluate
from ragas.dataset_schema import SingleTurnSample
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import (
    AnswerRelevancy,
    Faithfulness,
    LLMContextPrecisionWithReference,
    LLMContextRecall,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "runtime" / "reports" / "ragas-v02.json"


class DashScopeTextEmbeddings(Embeddings):
    """LangChain-compatible adapter for DashScope's native embedding API."""

    endpoint = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def _embed(self, texts: list[str], text_type: str) -> list[list[float]]:
        response = requests.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "input": {"texts": [str(text) for text in texts]},
                "parameters": {"text_type": text_type},
            },
            timeout=90,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("output", {}).get("embeddings", [])
        rows = sorted(rows, key=lambda item: item.get("text_index", 0))
        vectors = [item["embedding"] for item in rows]
        if len(vectors) != len(texts):
            raise RuntimeError(f"DashScope embeddings count mismatch: {len(vectors)} != {len(texts)}")
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]


def _load_samples(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or len(payload) != 1:
        raise ValueError("samples JSON must contain exactly one retrieval method")
    rows = next(iter(payload.values()))
    if not isinstance(rows, list) or not rows:
        raise ValueError("samples JSON is empty")
    return rows


def _dataset(rows: list[dict[str, Any]]) -> EvaluationDataset:
    return EvaluationDataset(
        samples=[
            SingleTurnSample(
                user_input=str(row["question"]),
                response=str(row["answer"]),
                retrieved_contexts=[str(context) for context in row["contexts"]],
                reference=str(row["ground_truth"]),
            )
            for row in rows
        ]
    )


def run(samples_path: Path, *, judge_model: str, embedding_model: str) -> dict[str, Any]:
    load_dotenv(ROOT / ".env", override=True)
    judge_key = os.getenv("OPENAI_API_KEY")
    judge_base = os.getenv("OPENAI_API_BASE")
    embedding_key = os.getenv("RAGAS_EMBEDDING_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if not judge_key or not judge_base:
        raise RuntimeError("OPENAI_API_KEY and OPENAI_API_BASE are required for the configured judge")
    if not embedding_key:
        raise RuntimeError("DASHSCOPE_API_KEY or RAGAS_EMBEDDING_API_KEY is required")

    llm = ChatOpenAI(
        model=judge_model,
        api_key=judge_key,
        base_url=judge_base,
        temperature=0,
        max_retries=1,
        request_timeout=90,
    )
    ragas_llm = LangchainLLMWrapper(llm)
    embeddings = LangchainEmbeddingsWrapper(DashScopeTextEmbeddings(embedding_key, embedding_model))
    metrics = [
        Faithfulness(llm=ragas_llm),
        AnswerRelevancy(llm=ragas_llm, embeddings=embeddings, strictness=1),
        LLMContextRecall(llm=ragas_llm),
        LLMContextPrecisionWithReference(name="context_precision", llm=ragas_llm),
    ]
    rows = _load_samples(samples_path)
    result = evaluate(
        _dataset(rows),
        metrics=metrics,
        llm=ragas_llm,
        embeddings=embeddings,
        raise_exceptions=True,
        show_progress=True,
        batch_size=1,
    )
    detail_rows = result.to_pandas().to_dict(orient="records")
    metric_names = ("faithfulness", "answer_relevancy", "context_recall", "context_precision")
    summary = {
        name: round(mean(float(row[name]) for row in detail_rows if row.get(name) is not None), 4)
        for name in metric_names
    }
    return {
        "ragas_version": "0.2",
        "samples": len(rows),
        "judge_model": judge_model,
        "embedding_model": embedding_model,
        "answer_relevancy_strictness": 1,
        "summary": summary,
        "details": detail_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--judge-model", default=os.getenv("RAGAS_JUDGE_MODEL", "deepseek-chat"))
    parser.add_argument("--embedding-model", default=os.getenv("RAGAS_EMBEDDING_MODEL", "text-embedding-v3"))
    args = parser.parse_args()
    report = run(args.samples, judge_model=args.judge_model, embedding_model=args.embedding_model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    print(f"out={args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
