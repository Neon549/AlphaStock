# Remote News RAGAS Evaluation

Date: 2026-08-13
Corpus: Tencent Cloud PostgreSQL through an SSH tunnel; 10 fixed internal
public-news evaluation questions. The set is useful for regression comparison,
but it is not a human-Gold or production accuracy benchmark.

## Environment and method

- RAGAS `0.2.15`, run in an isolated Python 3.13 environment from
  [`requirements-ragas.txt`](../requirements-ragas.txt).
- Judge: project-configured OpenAI-compatible `deepseek-chat` endpoint.
- Embedding for Answer Relevancy: DashScope `text-embedding-v3`.
- Answer Relevancy strictness: `1`, because the compatible judge endpoint does
  not support RAGAS' default multi-completion request.
- The generated answer was constrained to use only the supplied evidence and
  state when evidence was insufficient.

## Top-10 diagnostic candidate pool

| Retrieval corpus | Faithfulness | Answer Relevancy | Context Recall | Context Precision |
|---|---:|---:|---:|---:|
| News only, stock-scoped BM25 | 0.8533 | 0.6802 | 0.5667 | **0.6562** |
| News + official announcements + faceted BM25 | **0.9660** | **0.8602** | **0.6333** | 0.4967 |
| Delta | +0.1127 | +0.1800 | +0.0666 | -0.1595 |

Official announcements improve factual support and evidence coverage in a
deeper candidate pool, but adding too many primary-source chunks reduces
ranked-context precision.

## Online Top-5 candidate pool

| Retrieval corpus | Faithfulness | Answer Relevancy | Context Recall | Context Precision |
|---|---:|---:|---:|---:|
| News only, stock-scoped BM25 | 0.9475 | 0.5878 | **0.4500** | **0.5643** |
| News + announcements + faceted BM25 | **0.9875** | **0.6676** | 0.3000 | 0.3828 |
| Delta | +0.0400 | +0.0798 | -0.1500 | -0.1815 |

Decision: production Top-5 uses **news-first retrieval**. Official
announcements remain indexed and are added only if stock-scoped news cannot
provide enough lexical candidates. This preserves the stronger online
Recall/Precision baseline while keeping primary-source evidence available for
fallback and deeper offline analysis.

## Reproducibility

The raw ignored runtime artifacts are:

- `runtime/reports/ragas-v02-news-only.json`
- `runtime/reports/ragas-v02-news-announcement-faceted.json`
- `runtime/reports/ragas-v02-news-only-k5.json`
- `runtime/reports/ragas-v02-news-announcement-faceted-k5.json`

Regenerate samples with `evaluation.prepare_remote_db_ragas_samples`, then run:

```bash
runtime/ragas-venv/Scripts/python.exe -m evaluation.run_ragas_v02 \
  --samples runtime/reports/ragas-news-only-k5-samples.json \
  --out runtime/reports/ragas-v02-news-only-k5.json
```
