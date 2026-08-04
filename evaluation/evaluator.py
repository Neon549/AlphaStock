#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evaluation/evaluator.py
RAG 检索评估脚本 —— 基于 RAGAS 框架

评估目标：
  对比 纯向量检索（pgvector only） vs 混合检索（BM25 + pgvector + RRF）
  在 Context Recall / Context Precision / Faithfulness / Answer Relevancy 上的差异

使用方式：
  cd Alpha_stock
  python evaluation/evaluator.py

依赖：
  pip install ragas datasets langchain-openai
  （RAGAS 内部用 LLM 评估 Faithfulness / Answer Relevancy，需要 LLM API）
"""

import os
import sys
import json
from pathlib import Path

# 把项目根目录加入 path，确保能 import db / rag 等模块
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(dotenv_path=ROOT / ".env", override=True)

# ── 1. 人工标注的评估数据集 ──────────────────────────────────────────
# 格式：每条包含 question / ground_truth（标准答案）/ stock_code
# 你可以按照这个格式继续往下加，建议 20 条以上
# 数据来源：从你的新闻库里选真实发生过的事件手工写

EVAL_DATASET = [
    {
        "stock_code": "600519",
        "question": "贵州茅台最近有什么提价或价格相关消息？",
        "ground_truth": "贵州茅台上调飞天茅台i茅台平台零售价及合同价，茅台调价后股价盘中大涨5%，白酒股集体跟涨，茅台批价回落。",
    },
    {
        "stock_code": "300750",
        "question": "宁德时代近期有哪些新公司或业务扩展消息？",
        "ground_truth": "宁德时代在惠州成立新能源公司注册资本256万元，旗下时代绿能成立新公司含多项物联网相关业务，先惠技术获得宁德时代约9.2亿元订单。",
    },
    {
        "stock_code": "000858",
        "question": "五粮液最近有什么业绩或分红消息？",
        "ground_truth": "五粮液预计2026年上半年净利87.3亿元到92亿元，同比增长88.8%到98.97%，回购价格调降至151.01元每股。",
    },
    {
        "stock_code": "600036",
        "question": "招商银行近期有什么人事变动或资金流动消息？",
        "ground_truth": "招商银行联席公司秘书何咏紫离任关秀妍接任，主力资金撤离银行板块，银行业AI应用日均Token消耗升至百亿量级。",
    },
    {
        "stock_code": "002415",
        "question": "海康威视近期有什么分红消息？",
        "ground_truth": "海康威视董事长提议实施2026年中期利润分配，拟每10股派息5.5元，年内分红将超119亿元，提议分红超50亿元。",
    },
    {
        "stock_code": "601138",
        "question": "工业富联最近有哪些资金流动消息？",
        "ground_truth": "计算机股资金净流入，工业富联相关板块受AI服务器需求带动，主力资金动向活跃。",
    },
    {
        "stock_code": "300124",
        "question": "汇川技术近期有哪些新业务或合作消息？",
        "ground_truth": "汇川技术等在青岛成立轨道交通设备公司，机械设备板块主力资金净流入14.27亿元，个股大宗交易活跃。",
    },
    {
        "stock_code": "600487",
        "question": "亨通光电近期有什么重要动态？",
        "ground_truth": "亨通光电个股大宗交易活跃，光缆及海底电缆业务持续推进，相关板块资金关注度提升。",
    },
    {
        "stock_code": "002475",
        "question": "立讯精密近期有什么回购或上市消息？",
        "ground_truth": "立讯精密已耗资约10亿元回购股份，H股于7月9日在香港联交所主板挂牌上市，电子行业资金流出压力较大。",
    },
    {
        "stock_code": "603501",
        "question": "韦尔股份近期有什么资金或业务动态？",
        "ground_truth": "韦尔股份图像传感器业务持续推进，电子板块资金流动活跃，个股大宗交易有所动作。",
    },
]


# ── 2. 纯向量检索（对照组）──────────────────────────────────────────


# NOTE: This legacy function still delegates to the hybrid entrypoint below.
# Its results must not be treated as a pgvector-only A/B baseline.  Keep the
# old report for historical reference only; use a frozen-corpus adapter and
# datasets/RAG_GOLDENSET.md before adding a release metric.
def vector_only_retrieve(stock_code: str, query: str, top_k: int = 5):
    """只用 pgvector 语义检索，不走 BM25"""
    from rag.retriever import hybrid_retrieve_news

    try:
        result_str = hybrid_retrieve_news(stock_code, query, top_k=top_k)
        if not result_str:
            return []
        return [line.strip() for line in result_str.split("\n") if line.strip()]
    except Exception as e:
        print(f"[vector_only] 失败: {e}")
        return []


# ── 3. 混合检索（实验组）────────────────────────────────────────────


def hybrid_retrieve(stock_code: str, query: str, top_k: int = 5):
    """BM25 + pgvector + RRF 混合检索"""
    from rag.retriever import hybrid_retrieve_news

    try:
        result_str = hybrid_retrieve_news(stock_code, query, top_k=top_k)
        return result_str.split("\n") if result_str else []
    except Exception as e:
        print(f"[混合检索] {stock_code} 失败: {e}")
        return []


# ── 4. 用 DeepSeek API 生成答案 ─────────────────────────────────────


def generate_answer(question: str, contexts: list[str]) -> str:
    """基于检索到的 context 生成答案"""
    from config.llm_config import quick_llm as llm

    context_str = "\n".join(contexts) if contexts else "无相关新闻"
    prompt = f"""你是一个 A 股分析助手，请根据以下新闻内容回答用户问题。
如果新闻中没有相关信息，请回答"根据现有信息无法判断"。

新闻内容：
{context_str}

用户问题：{question}

请用 1-3 句话简洁回答："""

    try:
        response = llm.invoke(prompt)
        return getattr(response, "content", str(response))
    except Exception as e:
        return f"生成失败: {e}"


# ── 5. RAGAS 评估 ────────────────────────────────────────────────────


def run_ragas_eval(samples: list[dict], label: str) -> dict:
    """
    运行 RAGAS 评估
    samples: [{"question", "answer", "contexts", "ground_truth"}, ...]
    """
    try:
        from ragas import evaluate
        from ragas.metrics import (
            faithfulness,
            answer_relevancy,
            context_recall,
            context_precision,
        )
        from datasets import Dataset
        from langchain_openai import ChatOpenAI
        from ragas.llms import LangchainLLMWrapper
        import os

        llm = ChatOpenAI(
            model="deepseek-chat",
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_api_base=os.getenv("OPENAI_API_BASE"),
        )
        ragas_llm = LangchainLLMWrapper(llm)
        for m in [faithfulness, answer_relevancy, context_recall, context_precision]:
            m.llm = ragas_llm

        dataset = Dataset.from_list(samples)
        print(f"\n[{label}] 开始 RAGAS 评估，共 {len(samples)} 条样本...")

        result = evaluate(
            dataset,
            metrics=[faithfulness, answer_relevancy, context_recall, context_precision],
        )
        return dict(result)

    except ImportError:
        print("[警告] ragas 未安装，跳过 RAGAS 指标，只输出检索结果对比")
        return {}
    except Exception as e:
        print(f"[RAGAS 评估失败] {e}")
        return {}


# ── 6. 简单手动评估（不依赖 RAGAS）────────────────────────────────


def manual_context_recall(contexts: list[str], ground_truth: str) -> float:
    """
    简化版 Context Recall：
    检查 ground_truth 中的关键词有多少出现在 contexts 里
    这是一个粗略估计，不需要 LLM，可以离线跑
    """
    import jieba

    gt_tokens = set(jieba.cut(ground_truth))
    gt_tokens = {t for t in gt_tokens if len(t) > 1}  # 过滤单字

    if not gt_tokens:
        return 0.0

    context_text = " ".join(contexts)
    hit = sum(1 for t in gt_tokens if t in context_text)
    return round(hit / len(gt_tokens), 3)


# ── 7. 主流程 ────────────────────────────────────────────────────────


def main():
    print("=" * 60)
    print("StockMind RAG 评估脚本")
    print("对比：纯向量检索 vs 混合检索（BM25 + pgvector + RRF）")
    print("=" * 60)

    vector_samples = []
    hybrid_samples = []
    manual_results = []

    for i, item in enumerate(EVAL_DATASET):
        stock_code = item["stock_code"]
        question = item["question"]
        ground_truth = item["ground_truth"]

        print(f"\n[{i+1}/{len(EVAL_DATASET)}] {stock_code} - {question[:20]}...")

        # 纯向量检索
        v_contexts = vector_only_retrieve(stock_code, question)
        v_answer = generate_answer(question, v_contexts)

        # 混合检索
        h_contexts = hybrid_retrieve(stock_code, question)
        h_answer = generate_answer(question, h_contexts)

        # 手动 Context Recall（离线，不需要 RAGAS）
        v_recall = manual_context_recall(v_contexts, ground_truth)
        h_recall = manual_context_recall(h_contexts, ground_truth)

        manual_results.append(
            {
                "stock_code": stock_code,
                "question": question,
                "vector_recall": v_recall,
                "hybrid_recall": h_recall,
                "recall_delta": round(h_recall - v_recall, 3),
            }
        )

        print(f"  向量检索 Context Recall: {v_recall}")
        print(f"  混合检索 Context Recall: {h_recall}")
        print(f"  提升: {h_recall - v_recall:+.3f}")

        # 为 RAGAS 准备数据
        vector_samples.append(
            {
                "question": question,
                "answer": v_answer,
                "contexts": v_contexts,
                "ground_truth": ground_truth,
            }
        )
        hybrid_samples.append(
            {
                "question": question,
                "answer": h_answer,
                "contexts": h_contexts,
                "ground_truth": ground_truth,
            }
        )

    # ── 汇总手动评估结果 ──
    print("\n" + "=" * 60)
    print("手动评估汇总（基于关键词匹配的 Context Recall）")
    print("=" * 60)

    avg_v = sum(r["vector_recall"] for r in manual_results) / len(manual_results)
    avg_h = sum(r["hybrid_recall"] for r in manual_results) / len(manual_results)

    print(f"纯向量检索  平均 Context Recall: {avg_v:.3f}")
    print(f"混合检索    平均 Context Recall: {avg_h:.3f}")
    print(
        f"提升幅度:   {(avg_h - avg_v):.3f}  ({(avg_h-avg_v)/max(avg_v,0.001)*100:.1f}%)"
    )

    # ── 保存结果到 JSON ──
    output = {
        "summary": {
            "vector_avg_recall": round(avg_v, 3),
            "hybrid_avg_recall": round(avg_h, 3),
            "improvement": round(avg_h - avg_v, 3),
            "improvement_pct": round((avg_h - avg_v) / max(avg_v, 0.001) * 100, 1),
        },
        "details": manual_results,
    }

    out_path = ROOT / "evaluation" / "eval_results.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到: {out_path}")

    # ── 可选：跑 RAGAS（需要 pip install ragas 且有 LLM API）──
    run_ragas = os.getenv("RUN_RAGAS", "false").lower() == "true"
    if run_ragas:
        print("\n开始 RAGAS 完整评估（需要 LLM API，耗时较长）...")
        v_ragas = run_ragas_eval(vector_samples, "纯向量检索")
        h_ragas = run_ragas_eval(hybrid_samples, "混合检索")

        print("\nRAGAS 结果对比：")
        print(f"{'指标':<25} {'纯向量':>10} {'混合检索':>10} {'提升':>10}")
        print("-" * 55)
        for metric in [
            "faithfulness",
            "answer_relevancy",
            "context_recall",
            "context_precision",
        ]:
            v_val = v_ragas.get(metric, "N/A")
            h_val = h_ragas.get(metric, "N/A")
            if isinstance(v_val, float) and isinstance(h_val, float):
                delta = f"{h_val - v_val:+.3f}"
            else:
                delta = "N/A"
            print(f"{metric:<25} {str(v_val):>10} {str(h_val):>10} {delta:>10}")

        # 保存 RAGAS 结果
        ragas_out = ROOT / "evaluation" / "ragas_results.json"
        with open(ragas_out, "w", encoding="utf-8") as f:
            json.dump(
                {"vector": v_ragas, "hybrid": h_ragas}, f, ensure_ascii=False, indent=2
            )
        print(f"RAGAS 结果已保存到: {ragas_out}")

    print("\n✅ 评估完成")


if __name__ == "__main__":
    main()
