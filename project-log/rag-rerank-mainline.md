# BGE Cross-Encoder news rerank mainline

Date: 2026-08-13

## Production change

- First stage remains stock-scoped, news-first BM25.  Official announcements
  remain a fill-only source when there are too few news candidates.
- The default second stage is the locally cached
  `BAAI/bge-reranker-v2-m3` Cross-Encoder.  It reranks a bounded Top-5 news
  candidate pool and returns Top-5.
- Multi-facet questions retain one title-matched candidate per detected facet
  before the rest of the Top-5 is filled by Cross-Encoder score.
- The model is lazy-loaded once, runs fully offline, and uses CUDA when
  available.  A missing model or inference error falls back to scoped BM25;
  retrieval never fails because the reranker is unavailable.
- Trace metadata now includes the rerank method, model and candidate-pool
  size.  Raw news content remains hashed/redacted as before.

## Verification

- Local model smoke test passed: cached BGE model loaded and ranked a matching
  passage above an unrelated passage.
- Regression tests: `23 passed` for news retriever and retrieval-golden tests.
- Remote read-only news snapshot: 1,408 news rows, fixed 10-question set,
  Top-5, candidate pool 20.  Keyword coverage diagnostic was `0.4253` for
  `bm25_scoped_bge_reranked`, versus `0.5167` for scoped BM25.  This is a
  diagnostic only, not RAGAS or answer accuracy.

## RAGAS A/B result

The approved remote evaluation completed on the same 10 questions, Top-5
evidence, generator, judge and embedding model:

| Strategy | Faithfulness | Answer Relevancy | Context Recall | Context Precision |
| --- | ---: | ---: | ---: | ---: |
| Stock-scoped BM25 | 0.9653 | 0.5899 | 0.4500 | 0.6287 |
| BM25 + BGE Cross-Encoder | 0.9375 | 0.6829 | 0.3417 | 0.6056 |

BGE improves answer relevancy but regresses evidence coverage and precision on
this fixed snapshot.  It is therefore implemented and observable, but is not
quality-proven; the next iteration must tune candidate preservation or use
query-relevant body snippets before making an improvement claim.  Details:
[`evaluation/BGE_NEWS_RERANK_REMOTE_REPORT.md`](../evaluation/BGE_NEWS_RERANK_REMOTE_REPORT.md).

### Production tuning after the A/B

The default candidate pool is Top-5 (overridable through
`NEWS_RERANK_CANDIDATE_K`); Top-20 remains an offline experiment.  Candidate-5
RAGAS was `0.9081 / 0.5974 / 0.4667 / 0.5389` for Faithfulness, Answer
Relevancy, Context Recall and Context Precision.  Against BM25's
`0.9653 / 0.5899 / 0.4500 / 0.6287`, it improves recall and answer relevancy
but not faithfulness or precision.  This is the smallest bounded BGE default;
it does not justify an all-metric improvement claim.

The 50/50 BGE/BM25 score blend was also evaluated because it changed 6 of 10
candidate-5 rankings.  Its RAGAS result was `0.8507 / 0.5815 / 0.4333 /
0.5389`, worse than pure BGE Candidate-5, so production retains the pure BGE
weight of `1.0`.

## Entity verification and set-preserving production rerank

The raw remote snapshot contained sector listicles and unrelated-company
headlines stored against the queried code.  Production now gates persisted
news by ticker/current name or a trusted alias from official disclosure
metadata; live news is gated by ticker/current local name.  This reduces the
same evaluation snapshot from 1,408 raw rows to 348 verified rows and keeps
official announcements as fill-only evidence.

The default BGE pass no longer expands or replaces the lexical/facet Top-5.
It only reorders the exact evidence set chosen by BM25; setting a wider
candidate-pool environment value is an explicit offline experiment.  The
latest entity-verified RAGAS comparison was:

| Strategy | Faithfulness | Answer Relevancy | Context Recall | Context Precision |
| --- | ---: | ---: | ---: | ---: |
| BM25/facet order | **0.9381** | **0.7950** | 0.3833 | 0.5693 |
| Default BGE safe reorder | 0.8445 | 0.7816 | **0.4500** | **0.6656** |

This validates the guardrail (BGE no longer loses evidence coverage), but it
does not establish an all-metric improvement.  BGE remains the default local
production reranker as requested, with method telemetry and BM25 fallback.
