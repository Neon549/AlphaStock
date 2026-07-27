# -*- coding: utf-8 -*-
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv

load_dotenv()

test_cases = [
    {
        "question": "what is KDJ strategy signal",
        "ground_truth": "KDJ is a technical indicator using K D J values to signal buy or sell",
    },
    {
        "question": "stock technical analysis volume price",
        "ground_truth": "technical analysis uses price and volume data to predict trends",
    },
    {
        "question": "investment strategy risk management",
        "ground_truth": "investment strategy involves risk control and position sizing",
    },
]

from rag.strategy_indexer import retrieve_strategy_knowledge

questions, answers, contexts, ground_truths = [], [], [], []
for case in test_cases:
    q = case["question"]
    retrieved = retrieve_strategy_knowledge(q, k=3)
    questions.append(q)
    contexts.append([retrieved])
    answers.append(retrieved[:300] if retrieved else "no content")
    ground_truths.append(case["ground_truth"])
    print(f"retrieved: {q[:30]} ({len(retrieved)} chars)")

print("\nStarting RAGAS evaluation...")

from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_recall,
    context_precision,
)
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

judge_llm = ChatOpenAI(
    model="qwen-plus",
    openai_api_key=os.getenv("QWEN_API_KEY"),
    openai_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    temperature=0,
)

from ragas.embeddings import HuggingfaceEmbeddings

embeddings = HuggingfaceEmbeddings(
    model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)

dataset = Dataset.from_dict(
    {
        "question": questions,
        "answer": answers,
        "contexts": contexts,
        "ground_truth": ground_truths,
    }
)

result = evaluate(
    dataset,
    metrics=[faithfulness, answer_relevancy, context_recall, context_precision],
    llm=judge_llm,
    embeddings=embeddings,
)

print("\n=== RAGAS Results ===")
for k, v in result.items():
    print(f"{k}: {v:.4f}")
