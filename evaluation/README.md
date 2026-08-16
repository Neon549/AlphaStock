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

The business-quality release gate is now a separate, fail-closed check. It
must consume a frozen report containing code/governance regression, RAG and
E2E non-regression, citation accuracy, P95 latency, token/cost budgets and
red-team results:

```bash
python -m evaluation.release_quality_gate \
  --report runtime/reports/release-quality-gate-input.json \
  --out runtime/reports/release-quality-gate.json
```

Missing checks block the release. `release_allowed` is only a statement that
the supplied candidate passed its declared budgets; it is not a production
quality claim. The Chinese de-identified Gold contract is validated separately:

```bash
python -m evaluation.production_gold_intake \
  --dataset path/to/gold.jsonl --kind rag --require-dual-review \
  --out runtime/reports/gold-intake.json
```

Only `deidentified_session` and `production_bad_case` sources are accepted.
Every row carries a split, corpus hash, evidence IDs, page citations, answer
facts, abstention policy and review metadata. No candidate fixture can be
promoted by changing its label; the untouched test split still needs a final
manifest admission.

For recorded runtime telemetry, aggregate operational SLOs without calling
production:

```bash
python -m evaluation.operational_slo \
  --runs runtime/reports/agent-telemetry.jsonl \
  --out runtime/reports/operational-slo.json
```

The input must explicitly include concurrency, latency, provider/tool failure,
retry, fallback, token and cost fields. Missing telemetry fails closed rather
than being counted as zero failures.

Safety red-team runs are scored separately and can feed the release gate:

```bash
python -m evaluation.red_team_eval \
  --cases runtime/reports/red-team-cases.jsonl \
  --runs runtime/reports/red-team-runs.jsonl \
  --out runtime/reports/red-team.json
```

The evaluator only checks recorded traces and never creates an attack or runs a
tool. `quality_gate_input` exposes the total case count and high-risk failures;
an empty or unknown run set is invalid.

`rag_golden_eval.py` now provides deterministic snapshot scoring for
Recall@K, MRR, nDCG, citation hit rate, citation backlink correctness,
abstention compliance and unsupported-answer rate. Its committed
`fixtures/rag_corpus_v1.jsonl` plus `datasets/rag_golden_seed.jsonl` are a
small contract fixture, not a claim of production quality.

## News BGE rerank evaluation

The production news path uses entity-verified, stock-scoped BM25 followed by
the locally cached `BAAI/bge-reranker-v2-m3` Cross-Encoder. By default BGE only
reorders the exact BM25/facet Top-5 evidence set; it cannot introduce a new
document. Missing or malformed local-model output falls back to BM25 at
runtime. A wider candidate pool is an explicit offline experiment.

Reproduce the read-only remote snapshot diagnostic through the local SSH
tunnel (the command does not write to PostgreSQL):

```powershell
python -m evaluation.run_remote_db_retrieval_eval `
  --top-k 5 --candidate-k 5 --evidence-mode online `
  --out runtime/reports/remote-db-retrieval-ablation.json
```

The report includes `bge_comparison`, which compares each BGE method with
`bm25_scoped_faceted` using the fixed-set keyword-context diagnostic. It is a
diagnostic only. To run the separate LLM/RAGAS layer, first generate answer
samples from that exact report with `evaluation.prepare_remote_db_ragas_samples`,
then run `evaluation.run_ragas_v02` in the isolated RAGAS environment. Do not
interpret a single improved RAGAS metric as a universal quality lift.

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

### 中文主基准：CFQA

项目的中文年报问答主基准是 CFQA，而不是英文 FinanceBench。CFQA 提供中文上市
公司年报问题、答案和答案页码，适合检验公司/报告定位、中文术语召回、页级引用和
后续的表格数值推理。当前快照和本次 BM25、中文向量、BGE 对照结果见
[`CFQA 中文财报 RAG 评测报告`](datasets/CFQA_RAG_REPORT.md)。

重新拉取并固定源仓库版本：

```powershell
$commit = (git -c safe.directory=runtime/external/CFQA -C runtime/external/CFQA rev-parse HEAD).Trim()
python -B evaluation/import_external_public_qa.py `
  --repo runtime/external/CFQA `
  --dataset cfqa `
  --split test `
  --commit $commit `
  --output runtime/external/cfqa_test_candidates.jsonl
```

导入后的 2,036 条记录仍是待映射候选。只有下载并固定对应的官方年报、把 CFQA
页码映射到 Evidence ID，并完成独立人工复核后，才可以作为正式 RAG Gold。当前
已页锚定的 20 条扩展样本可运行，完整解析、下载和评测命令见
[`CFQA 中文财报 RAG 评测报告`](datasets/CFQA_RAG_REPORT.md)。原有 9 条 v1 样本仍保留作历史对照：

```powershell
python -m evaluation.run_candidate_rag_eval `
  --cases evaluation/corpus/external_cfqa_v1/rag_validation_candidates.jsonl `
  --chunks runtime/reports/external-cfqa-v1.chunks.jsonl `
  --source-manifest evaluation/corpus/external_cfqa_v1/sources.json `
  --k 10 `
  --methods bm25_global bm25_entity_period_scoped bm25_entity_period_scoped_alias `
  --dataset-tier external_cfqa_candidate_pending_independent_review `
  --out runtime/reports/external-cfqa-v1.bm25.rerun.json
```

### 可选英文对照：FinanceBench

FinanceBench 仅作为英文跨市场对照保留，不参与中文主结论。为避免把 AlphaStock
候选集误写成人工 Gold，FinanceBench 仍单独放在 `external_gold` 层。它提供 150
条公开金融问答，内容包括人工答案、人工解释、证据文本、从 0 开始的证据页码和
对应的 SEC 文件。它不是 AlphaStock 的线上流量，也不能转化为生产代表性结论。

生成固定的来源清单、150 条公开 Gold JSONL 和页级 PDF 语料：

```bash
python -m evaluation.import_financebench
```

运行 AlphaStock BM25 检索评测：

```bash
python -m evaluation.run_financebench_eval ^
  --out runtime/reports/financebench-v1.retrieval.json
```

如果需要更接近实际 RAG 索引的对照，可以把长 PDF 页面切成短检索块，同时保留
原始 Gold 页码回链：

```bash
python -m evaluation.build_financebench_chunks ^
  --out runtime/reports/financebench-v1.chunks-1200.jsonl
python -m evaluation.run_financebench_eval ^
  --chunks runtime/reports/financebench-v1.chunks-1200.jsonl ^
  --out runtime/reports/financebench-v1.chunks-1200.bm25.json
```

原始页级基线中，全库 BM25 的 Recall@10 为 13.67%，公司/报告期约束后为 23.67%。
这些数字只用于英文跨市场对照，不是线上用户准确率。来源和声明边界见
[`外部基准声明边界`](datasets/EXTERNAL_BENCHMARK_CLAIMS.md)。

## 端到端答案评测

`run_rag_e2e_eval` 评估完整路径：问题 -> 检索 -> 答案生成 -> 基准答案判定 ->
引用支撑。它不会把检索 Recall 或 RAGAS Faithfulness 误称为答案正确率。

英文 FinanceBench 的端到端示例（仅作可选对照）：

```powershell
$env:HF_HUB_OFFLINE = "1"
python -m evaluation.run_rag_e2e_eval `
  --k 20 `
  --retriever bm25_entity_period_scoped_reranked `
  --generator configured_llm `
  --judge configured_llm `
  --progress runtime/reports/financebench-v1.e2e.progress.json `
  --out runtime/reports/financebench-v1.e2e.json
```

报告会分开记录：

* `answer_accuracy`：基准判定器认为生成答案实质正确；
* `grounded_answer_accuracy`：答案正确、引用齐全，并且引用页确实被检索到；
* `retrieval_hit_rate_at_k`：检索到了标注证据，与模型是否答对无关。

配置的判定器是自动 LLM 判定器，适合可复现的工程基准，但不代表每个生成答案都
经过独立人工审核。FinanceBench 的参考答案和证据标注来自人工；AlphaStock 的
答案输出仍是机器判定，除非另行完成人工审计。CFQA 当前页锚定候选的
`judged_cases=0`，因此不报告答案正确率。

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
