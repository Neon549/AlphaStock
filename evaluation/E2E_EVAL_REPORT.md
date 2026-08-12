# AlphaStock end-to-end RAG evaluation

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

`Answer accuracy` is calculated over judgeable cases. `All-case lower bound`
counts API timeouts/unjudged cases as not correct, so it is the conservative
number. FinanceBench used the configured LLM answer judge; the Chinese numeric
candidate/heldout sets used the deterministic fact-value judge. Abstention
cases are retained in `cases` but excluded from `judged` because they do not
have answer facts to compare.

## Interpretation

The public benchmark is the difficult, externally comparable result. Its
43.24% answer accuracy and 30.41% citation-grounded accuracy show that the
system is functioning end to end but still has a substantial evidence and
multi-step answer gap. The smaller Chinese sets are engineering diagnostics,
not production Gold, and should not be presented as representative online
accuracy.

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
