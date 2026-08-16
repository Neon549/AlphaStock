# CFQA 中文财报 RAG 评测报告

更新时间：2026-08-16

## 结论摘要

CFQA 现在是 AlphaStock 的中文年报问答主评测来源。FinanceBench 保留在仓库中，
仅作为可选的英文跨市场对照，不参与中文主结论。

本次已从 CFQA 官方仓库重新抓取并固定版本：

- 仓库：[ygan/CFQA](https://github.com/ygan/CFQA)
- 许可证：MIT
- 固定提交：`61c9ec3c4335d0411a1735cd228af8b3ead114fc`
- 测试文件：`dataset/split_by_company/split_by_company_test.json`
- 测试文件 SHA-256：`869ec90815e2feb77763d0fa40317df139051d46987398b11ad12413be4dedb0`

论文报告的 CFQA 总规模是 13,643 条问题、516 份年报（见
[CFQA 论文](https://link.springer.com/article/10.1140/epjds/s13688-025-00601-6)）；
本次下载的 GitHub 快照按公司划分的测试文件包含 2,100 条原始记录，经过空问题/空答案过滤后得到
2,036 条可导入记录。论文规模和仓库当前快照不完全相同，因此本文只报告本次
实际固定的快照数字，不把论文数字冒充为本地运行结果。

## 数据分层

| 数据层 | 数量 | 当前状态 | 可支持的指标 |
|---|---:|---|---|
| CFQA 公司划分测试集 | 原始 2,100；可导入 2,036 | 已下载、已固定提交和哈希；年报 PDF 尚未全部映射 | 数据清单、导入完整性、后续页级评测入口 |
| CFQA 页码映射抽样队列 | 120 | 固定随机种子 `20260813`；已完成其中 20 条官方来源解析 | 映射进度，不是 RAG Gold |
| CFQA 页锚定检索子集 v1 | 9 条、7 份年报、3,319 个文档块 | 证据 ID 已生成；独立人工复核仍待完成 | 历史候选 Recall@10、MRR、NDCG、引用命中率 |
| CFQA 页锚定检索子集 v2 | 20 条、19 份年报、8,972 个文档块 | PDF 哈希锁定、Evidence ID 已生成；独立人工复核仍待完成 | 扩展候选 Hit@10、Recall@10、MRR、NDCG、引用命中率 |

2,036 条记录不是 2,036 条已经可直接评估的 RAG Gold。CFQA 给出了答案页码，
但 AlphaStock 仍必须下载匹配的官方年报、固定文件哈希和 PDF 页序，再把页码映射
到 Evidence ID；完成独立复核后才能提升为正式 Gold。

## 检索实验

实验对象是 20 条页锚定候选，`k=10`，标签完整性检查通过。结果如下：

| 方法 | Hit@10 | Recall@10 | MRR | NDCG@10 | 引用页命中率 |
|---|---:|---:|---:|---:|---:|
| BM25 全库 | 35.00% | 35.00% | 0.2221 | 0.2505 | 40.00% |
| BM25 公司/报告期约束 | 55.00% | 51.25% | 0.3760 | 0.4031 | 55.00% |
| BM25 公司/报告期约束 + 中文别名 | **65.00%** | **61.25%** | **0.4082** | **0.4513** | **65.00%** |
| BGE Cross-Encoder 重排 `BAAI/bge-reranker-v2-m3` | 45.00%* | — | — | — | — |

\* BGE 这一行来自端到端离线诊断的 `retrieval_hit_rate_at_k`，不是同一
候选评测器生成的 Recall 表格；它使用 BM25 候选池后再做 Cross-Encoder 重排。
同一端到端协议下，未使用 BGE 的 BM25 命中率为 55.00%，因此当前扩展样本中
BGE 仍然没有提升。

### 对结果的解释

当前最有效的是公司/报告期约束加金融术语别名扩展，说明本项目的主要瓶颈仍然
包括实体识别、报告定位和中文财务术语归一化。扩展到 20 条后，别名 BM25 的
Recall@10 从之前 9 条样本的 77.78% 回落到 61.25%，说明 9 条结果不能作为稳定
结论。BGE 在扩展样本上仍未提升，原因可能是候选池太小、表格结构没有显式编码、
页级证据和自然语言问题之间存在格式差异；这仍不等于 BGE 在完整 CFQA 上无效。

这次端到端脚本返回 `judged_cases=0`。原因是当前 CFQA 页锚定候选尚未完成
独立答案判定，不能据此报告 `answer_accuracy` 或 `grounded_answer_accuracy`。
当前报告只发布检索和引用页诊断，不发布答案正确率。

## 自动证据审计与人工复核队列

在不修改 Gold 标签的前提下，已对 v2 的 20 条候选运行确定性证据审计：

| 自动审计状态 | 数量 | 含义 |
|---|---:|---|
| `auto_accept_current` | 10 | 当前标注页通过页码、引用和部分事实锚点检查，可作为人工复核的优先通过候选 |
| `needs_review` | 10 | 当前页与答案、表格、数值或检索建议之间存在不确定性，必须人工打开 PDF 核对 |

自动审计不是人工审核，也不会把记录提升为 Gold；`auto_accept_current` 只表示
确定性规则没有发现明显冲突。所有 20 条记录仍已写入人工复核队列，审核人需要逐项确认：

1. CFQA 页码是否与下载 PDF 的页序一致；
2. 答案对应的表格行、列和报告期是否正确；
3. 数值的单位、正负号、百分比或计算关系是否完整；
4. 当前 Evidence ID 和引用页是否足以支持答案；
5. 证据不足时是否应该标记为拒答，而不是补写推断。

本轮需要优先检查的候选为：`cfqa-v1-002`、`003`、`004`、`006`、`008`、`009`、
`011`、`013`、`014`、`018`。这些记录的当前页仍保留在数据中，自动检索给出的其他页
只作为人工定位提示，不能直接替换当前标签。

自动审计和人工队列均为可重复生成的运行时产物：

```powershell
python -m evaluation.auto_review_rag_candidates `
  --cases runtime/reports/cfqa-v1-20.rag.jsonl `
  --chunks runtime/reports/cfqa-v1-20.chunks.jsonl `
  --source-manifest runtime/external/cfqa_v1_sources_resolved.json `
  --ablation runtime/reports/cfqa-v1-20.bm25.json `
  --top-k 10 `
  --out runtime/reports/cfqa-v1-20.auto-review.json

python -m evaluation.build_rag_label_review `
  --cases runtime/reports/cfqa-v1-20.rag.jsonl `
  --chunks runtime/reports/cfqa-v1-20.chunks.jsonl `
  --source-manifest runtime/external/cfqa_v1_sources_resolved.json `
  --ablation runtime/reports/cfqa-v1-20.bm25.json `
  --out runtime/reports/cfqa-v1-20.human-review-queue.json
```

只有在人工复核结果写回、具备审核人和审核时间，并通过 Evidence ID、页码、表格
结构及答案支撑检查后，才允许把候选数据集层级从 `candidate_pending_independent_review`
提升为正式 Gold。

## 复现实验

在项目根目录执行：

```powershell
git clone https://github.com/ygan/CFQA.git runtime/external/CFQA
$commit = (git -c safe.directory=runtime/external/CFQA -C runtime/external/CFQA rev-parse HEAD).Trim()
python -B evaluation/import_external_public_qa.py `
  --repo runtime/external/CFQA `
  --dataset cfqa `
  --split test `
  --commit $commit `
  --output runtime/external/cfqa_test_candidates.jsonl
```

解析 20 条页锚定候选对应的官方 CNINFO 年报来源：

```powershell
python -m evaluation.resolve_cfqa_sources `
  --input evaluation/corpus/external_cfqa_v1/candidates.jsonl `
  --output runtime/external/cfqa_v1_candidates_resolved.jsonl `
  --sources-out runtime/external/cfqa_v1_sources_resolved.json
```

下载并构建页级语料：

```powershell
python -m evaluation.download_corpus `
  --sources runtime/external/cfqa_v1_sources_resolved.json `
  --target-dir runtime/external/cfqa_v1_pdfs `
  --snapshot-out runtime/external/cfqa_v1_snapshot.json
python -m evaluation.build_pdf_corpus `
  --lock runtime/external/cfqa_v1_snapshot.json `
  --download-dir runtime/external/cfqa_v1_pdfs `
  --source-manifest runtime/external/cfqa_v1_sources_resolved.json `
  --chunks-out runtime/reports/cfqa-v1-20.chunks.jsonl `
  --metadata-out runtime/reports/cfqa-v1-20.corpus.json
$meta = Get-Content runtime/reports/cfqa-v1-20.corpus.json -Raw | ConvertFrom-Json
python -m evaluation.materialize_cfqa_gold `
  --candidates runtime/external/cfqa_v1_candidates_resolved.jsonl `
  --chunks runtime/reports/cfqa-v1-20.chunks.jsonl `
  --corpus-version $meta.candidate_index_snapshot `
  --output runtime/reports/cfqa-v1-20.rag.jsonl
```

运行扩展页锚定子集的稀疏检索对照：

```powershell
python -m evaluation.run_candidate_rag_eval `
  --cases runtime/reports/cfqa-v1-20.rag.jsonl `
  --chunks runtime/reports/cfqa-v1-20.chunks.jsonl `
  --source-manifest runtime/external/cfqa_v1_sources_resolved.json `
  --k 10 `
  --methods bm25_global bm25_entity_period_scoped bm25_entity_period_scoped_alias `
  --dataset-tier external_cfqa_candidate_pending_independent_review `
  --out runtime/reports/cfqa-v1-20.bm25.json
```

BGE 端到端离线诊断：

```powershell
$env:HF_HUB_OFFLINE = "1"
python -m evaluation.run_rag_e2e_eval `
  --cases runtime/reports/cfqa-v1-20.rag.jsonl `
  --chunks runtime/reports/cfqa-v1-20.chunks.jsonl `
  --source-manifest runtime/external/cfqa_v1_sources_resolved.json `
  --k 10 `
  --retriever bm25_entity_period_scoped_reranked `
  --generator extractive `
  --judge deterministic `
  --out runtime/reports/cfqa-v1-20.e2e.bge.deterministic.json
```

`runtime/external` 和 `runtime/reports` 是运行时目录，不把外部数据和生成报告
直接提交到代码仓库；依赖的提交号、源文件哈希、映射状态和评测协议保存在本文档
及数据集说明中。

## 下一步

1. 对已解析的 20 条候选逐条复核 PDF 页序、表格行列、单位和答案是否真的由该页支持。
2. 从剩余 100 条映射队列中继续扩展不同公司和报告年份的官方年报。
3. 为数值题补充分子、分母、单位和计算过程标签，之后才运行答案正确率与拒答评测。
4. 若 BGE 继续重排，先把 BM25 候选池扩大到 Top-50/Top-100，并加入表格结构和
   公司/报告期保护规则，避免正确证据被无约束重排挤出。
