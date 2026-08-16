# AlphaStock 端到端 RAG 评测

Run date: 2026-08-13. The pipeline is query → retrieval → rerank → answer
generation → answer judge → citation grounding. `answer_accuracy` is not
Recall@K and is not RAGAS Faithfulness.

## Results

| Dataset / tier | Cases | Judged | Answer accuracy | All-case lower bound | Page citation accuracy | Grounded answer accuracy | Retrieval hit@20 |
|---|---:|---:|---:|---:|---:|---:|---:|
| FinanceBench open-source v1 / external Gold | 150 | 148 | 43.24% | 42.67% | 42.57% | 30.41% | 64.67% |
| Production candidate v1 / internal candidate | 22 | 20 | 65.00% | 59.09% | 70.00% | 60.00% | 90.91% |
| Heldout public filings v1 / internal validation candidate | 25 | 22 | 81.82% | 72.00% | 90.91% | 72.73% | 96.00% |
| Heldout public filings v2 / internal validation candidate | 22 | 20 | 85.00% | 77.27% | 55.00% | 55.00% | 81.82% |
| CFQA 页锚定候选 / 中文主基准候选 | 9 | 0 | — | — | — | — | 55.56%* |

`Answer accuracy` is calculated over judgeable cases. `All-case lower bound`
counts API timeouts/unjudged cases as not correct, so it is the conservative
number. FinanceBench used the configured LLM answer judge; the Chinese numeric
candidate/heldout sets used the deterministic fact-value judge. Abstention
cases are retained in `cases` but excluded from `judged` because they do not
have answer facts to compare.

\* CFQA 行是 2026-08-16 重新抓取后的 9 条页锚定候选检索诊断；它尚未完成独立
答案判定，所以 `judged=0`，不能报告答案正确率。完整 BM25、中文向量和 BGE
对照见 [`datasets/CFQA_RAG_REPORT.md`](datasets/CFQA_RAG_REPORT.md)。

## Interpretation

FinanceBench 是英文跨市场对照结果，不是 AlphaStock 中文线上准确率。CFQA 现在
是中文主基准；当前 2,036 条记录中只有 9 条完成页锚定，且仍是候选诊断，因此
不能把它写成生产 Gold 或代表性线上准确率。

The existing internal RAGAS Faithfulness result (Hybrid 95.2%) remains a
separate support metric: it means the answer is faithful to retrieved context,
not that the answer is correct relative to a benchmark reference.

## Reproducibility artifacts

* `runtime/reports/financebench-v1.e2e.full.page-citations.json`
* `runtime/reports/production-candidate-v1.e2e.page-citations.json`
* `runtime/reports/heldout-public-filings-v1.e2e.page-citations.json`
* `runtime/reports/heldout-public-filings-v2.e2e.page-citations.json`
* `evaluation/run_rag_e2e_eval.py`
* `evaluation/recompute_e2e_metrics.py`

The generated reports contain per-case answer, judge reason, retrieved IDs,
citation checks and grounded status. They should remain controlled artifacts
if the prompts or answers contain sensitive data.
