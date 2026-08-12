# Evaluation guide

## Dataset integrity and claim boundary

Before running or quoting an evaluation, verify the versioned dataset manifest:

```bash
python -m evaluation.dataset_manifest
```

The manifest pins exact JSONL bytes by case count and SHA-256. It currently
separates contract/smoke fixtures from two non-reportable candidate sets: 22
public-filing fact/abstention cases and 88 template-generated query robustness
variants. Candidate status means the corpus is real and frozen, not that its
labels represent production traffic or may support resume quality claims.

To make a reportable result, add a separate `production`-tier frozen dataset
with a corpus snapshot, a documented review protocol, source provenance, and
strict train/evaluation separation.  The manifest validator rejects a
production tier without that review protocol.

Use [the production admission protocol](datasets/PRODUCTION_EVAL_PROTOCOL.md)
and `python -m evaluation.frozen_dataset` to validate the RAG or routing JSONL
before it enters the manifest. Use `--tier smoke` only for integration checks;
the default `--tier production` requires reviewer provenance for every case.

## One offline report

Run the deterministic tiers together and retain the JSON next to the code
change being evaluated:

```bash
python -m evaluation.offline_report --out runtime/reports/offline-eval.json
```

It verifies the manifest, runs the fixed-corpus BM25 retrieval fixture, checks
citation/abstention governance, and runs workflow safety regression. Routing
is opt-in (`--include-routing`) because its LLM fallback makes it a separate,
non-deterministic smoke result rather than a release gate.

`evaluator.py` and its JSON reports are historical exploratory artifacts, not
release gates: its legacy vector-only function shares a hybrid entrypoint and
the news source is live. Do not use those stored scores as a reproducible
pgvector-versus-hybrid comparison.

The current offline release gate is:

```bash
python evaluation/regression_runner.py
```

It checks deterministic workflow controls and runs in GitHub Actions before a
main-branch deployment. The 8 committed cases are intentionally a smoke set:
all must pass.

`rag_golden_eval.py` now provides deterministic snapshot scoring for
Recall@K, MRR, nDCG, citation hit rate, citation backlink correctness,
abstention compliance and unsupported-answer rate. Its committed
`fixtures/rag_corpus_v1.jsonl` plus `datasets/rag_golden_seed.jsonl` are a
small contract fixture, not a claim of production quality.

## Public-filing candidate workflow

The current candidate corpus pins 10 official annual-report PDFs (2,561 pages)
and builds 5,534 page-citable chunks. Reproduce the local artifacts with:

```bash
python -m evaluation.download_corpus --snapshot-out evaluation/corpus/production_candidate_v1/SNAPSHOT.json
python -m evaluation.corpus_preflight --out runtime/reports/public-filings-candidate-v1.preflight.json
python -m evaluation.build_pdf_corpus --chunks-out runtime/reports/public-filings-candidate-v1.chunks.jsonl --metadata-out runtime/reports/public-filings-candidate-v1.corpus.json
python -m evaluation.run_candidate_rag_eval --methods bm25_global bm25_entity_period_scoped dense_entity_period_scoped hybrid_rrf_entity_period_scoped --out runtime/reports/public-filings-candidate-v1.retrieval-ablation.json
python -m evaluation.build_rag_label_review --out runtime/reports/public-filings-candidate-v1.label-review.json
python -m evaluation.build_rag_query_variants
```

The label review queue separates strict single-ID retrieval from alternative
answer-bearing evidence. Its fact-value diagnostics are label-derived audit
aids, not headline metrics. The set can move from `candidate` to `production`
only after an independent reviewer accepts evidence IDs and query variants,
the frozen manifest is updated, and train/evaluation separation is recorded.

### Human review and promotion

The 22 candidate cases have already been used for retriever selection. They
must therefore become a reviewed **validation** set, not the final untouched
test set. Generate an explicit, one-row-per-case review queue locally:

```bash
python -m evaluation.rag_review_workflow ^
  --review-template-out runtime/reports/public-filings-candidate-v1.human-review.jsonl
```

After an independent reviewer fills every row with `decision: approved`, a
reviewer name, ISO review date, approved evidence IDs and page citations,
promote it into a separately versioned validation JSONL:

```bash
python -m evaluation.rag_review_workflow ^
  --reviews runtime/reports/public-filings-candidate-v1.human-review.jsonl ^
  --promoted-out evaluation/datasets/rag-public-filings-validation-v1.jsonl
python -m evaluation.frozen_dataset --kind rag --tier production ^
  --dataset evaluation/datasets/rag-public-filings-validation-v1.jsonl
```

Do not create a `test` split by relabeling this set. Collect an independently
reviewed, never-tuned real-query set for the final test before reporting an
interview- or resume-facing quality number.

The review-queue helper also accepts non-default corpora when the matching
source manifest is passed explicitly. Rows with a `reference_answer` receive
lexical candidate evidence pages (including numeric anchors) to help detect
page-mapping errors; these suggestions remain `pending_human_review` and must
not be auto-approved:

```bash
python -m evaluation.build_rag_label_review ^
  --cases evaluation/corpus/external_cfqa_v1/rag_validation_candidates.jsonl ^
  --chunks runtime/reports/external-cfqa-v1.chunks.jsonl ^
  --source-manifest evaluation/corpus/external_cfqa_v1/sources.json ^
  --ablation runtime/reports/external-cfqa-v1.alias-v3.json ^
  --out runtime/reports/external-cfqa-v1.human-review.json
```

If there is no time for row-by-row review, run the sidecar deterministic audit
instead. It does not edit the cases or rerun/alter the retrieval metrics. It
classifies rows as `auto_accept_current`, `auto_repair_candidate`,
`auto_accept_abstention`, or `needs_review`; automatic results remain a
candidate engineering report and cannot be promoted as human-reviewed Gold:

```bash
python -m evaluation.auto_review_rag_candidates ^
  --cases evaluation/corpus/production_candidate_v1/rag_candidates.jsonl ^
  --chunks runtime/reports/public-filings-candidate-v1.chunks.jsonl ^
  --source-manifest evaluation/corpus/production_candidate_v1/sources.json ^
  --ablation runtime/reports/public-filings-candidate-v1.retrieval-ablation.json ^
  --out runtime/reports/public-filings-candidate-v1.auto-review.json

python -m evaluation.auto_review_rag_candidates ^
  --cases evaluation/corpus/external_cfqa_v1/rag_validation_candidates.jsonl ^
  --chunks runtime/reports/external-cfqa-v1.chunks.jsonl ^
  --source-manifest evaluation/corpus/external_cfqa_v1/sources.json ^
  --ablation runtime/reports/external-cfqa-v1.alias-v3.json ^
  --out runtime/reports/external-cfqa-v1.auto-review.json
```

Use the automatic report to continue retrieval engineering and expose likely
page-mapping repairs. Keep the existing candidate retrieval report as the
source of Recall/MRR/NDCG/citation metrics; the sidecar is deliberately not
fed back into those calculations.

Use [the real-query collection card](datasets/RAG_REAL_QUERY_COLLECTION_TEMPLATE.md)
and `python -m evaluation.real_rag_test_admission` before freezing that final
test. The admission audit blocks identity fields/common PII patterns and any
overlap with the current retriever-selection datasets by query, cited document
or labelled fact.

### Public human-annotated external benchmark

To support an externally reproducible resume or interview claim without
pretending that the AlphaStock candidate cases are human Gold, the project
also imports the open-source FinanceBench sample. FinanceBench provides 150
public financial QA cases with human answers, human justifications, evidence
text, zero-indexed evidence pages, and the corresponding SEC-filing PDFs. The
imported cases are kept under the separate `external_gold` tier; they are not
AlphaStock online traffic and do not become a production-representative
claim.

Generate the pinned source manifest, 150-case public Gold JSONL, and page-level
PDF corpus:

```bash
python -m evaluation.import_financebench
```

Run the unchanged AlphaStock BM25 retrieval evaluation on that external Gold:

```bash
python -m evaluation.run_financebench_eval ^
  --out runtime/reports/financebench-v1.retrieval.json
```

For a more realistic RAG index, split long PDF pages into short retrievable
chunks while preserving the original Gold-page backlink, then run the same
page-level protocol:

```bash
python -m evaluation.build_financebench_chunks ^
  --out runtime/reports/financebench-v1.chunks-1200.jsonl
python -m evaluation.run_financebench_eval ^
  --chunks runtime/reports/financebench-v1.chunks-1200.jsonl ^
  --out runtime/reports/financebench-v1.chunks-1200.bm25.json
```

The original page baseline is Recall@10 13.67% for global BM25 and 23.67%
after deterministic company/report-period scoping. On the 1,200-character
page-citable chunks, those same retrievers reached 14.33% and 26.00%; scoped
Recall@100 was 53.78%, which identifies ranking (rather than candidate
discovery alone) as the next bottleneck. An optional CPU-feasible English
Dense/RRF run uses `--embedding-model bge_small_en_v1_5`; `bge_m3` remains the
multilingual reference model for a GPU-capable runner. These are external
benchmark retrieval results, not online-user accuracy. The source benchmark and its
annotation fields are documented in the [FinanceBench repository](https://github.com/patronus-ai/financebench).
See [external benchmark reporting and resume wording](datasets/EXTERNAL_BENCHMARK_CLAIMS.md)
for the exact claim boundaries and current protocol-specific numbers.

### Real user-query intake is not only RAG

The initial 13 manual expert queries are recorded in
`evaluation/datasets/user_query_intake_v1.jsonl`. They are deliberately kept
outside the RAG Golden Set until their target corpus/evidence is labelled. The
intake separates fact retrieval from live
document/news verification, high-risk research decisions, multi-entity
comparison, product support and clarification. Validate its privacy and
coverage boundary with:

```bash
python -m evaluation.user_query_intake
```

Every retrieval report also includes deterministic bootstrap 95% intervals and
source/tag slices. These quantify finite-sample uncertainty; they do not erase
candidate-data or real-traffic representativeness boundaries.

Retrieval reports distinguish `HitRate@K` (at least one Gold chunk returned)
from standard `Recall@K` (all labelled relevant chunks returned / all labelled
relevant chunks), and calculate strict `Precision@K` with a fixed K denominator,
per-query F1@K, MRR and nDCG. Precision is meaningful only after all acceptable
evidence chunks have been human-labelled; before then it is a label-coverage
diagnostic, not a headline score.

## Held-out public-filings development set

`heldout_public_filings_v1` is separate from the original 10-document
retriever-selection corpus. It currently contains three official filings
(中国船舶 2026Q1、中天科技 FY2025、天齐锂业 2025H1), 1,127 page-citable chunks,
22 manually authored fact questions and 3 wrong-period abstention questions.
Both JSONL and corpus snapshots are SHA-256 pinned in `DATASET_MANIFEST.json`.

```bash
python -m evaluation.run_heldout_rag_eval ^
  --k 10 --ks 1 3 5 10 ^
  --out runtime/reports/heldout-public-filings-v1.bm25.curve.json
```

The runner compares global BM25, entity/report-period scoped BM25, and scoped
BM25 with deterministic financial-field aliases. It reports the full
HitRate/Recall/Precision/F1 curve, MRR, nDCG, page-citation coverage and
wrong-period abstention compliance. Alias expansion maps wording such as
`归母` or `经营现金流` to filing field names; it is not an LLM rewrite and cannot
add facts.

This is still a held-out **development candidate**: the documents were not
used for initial retriever selection, but questions are manually authored and
Gold labels await independent review. Do not use its numbers as resume claims.

Once the untouched final dataset and its new source corpus are available, the
admitted BM25 baseline is run through a separate entrypoint. It stops before
retrieval if privacy, provenance, overlap or label-integrity checks fail:

```bash
python -m evaluation.run_final_rag_eval ^
  --cases evaluation/datasets/rag-real-final-test-v1.jsonl ^
  --chunks runtime/reports/rag-real-final-test-v1.chunks.jsonl ^
  --source-manifest evaluation/corpus/rag-real-final-test-v1/sources.json ^
  --out runtime/reports/rag-real-final-test-v1.bm25.json
```
