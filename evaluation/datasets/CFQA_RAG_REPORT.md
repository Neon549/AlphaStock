# CFQA 中文财报 RAG 评测报告

## CFQA v1 120 条扩展批次（2026-08-17）

为避免固定 10 条或 20 条样本被误读成完整结论，本轮从已固定的 CFQA 测试候选中抽取 120 条，按股票代码和报告年份解析官方 CNINFO 年报，并建立独立的页级检索语料。该批次仍属于 `candidate_pending_independent_review`，不是最终 Gold，也不用于简历或线上质量声明。

### 数据和可复现性

| 项目 | 结果 |
|---|---:|
| CFQA 候选题数 | 120 |
| 成功完成来源和页码物化 | 114 |
| 未纳入检索分母 | 6（5 条官方来源待补，1 条 PDF 页码超出当前解析页数） |
| 官方年报 PDF | 89 份 |
| PDF 解析器 | PyMuPDF |
| 检索块 | 40,864（600 字符，重叠 80） |
| 来源快照哈希 | `sha256:290e9cc3d6bd0dd105bc7cd6995a29a6d3a32134bda507fd59163239ad2fb467` |
| 检索语料哈希 | `sha256:c7e97afa3ba5e592e59b9f123be239165ba5ed247f9866e67006318622c94053` |

### BM25 页级检索基线（k=10）

| 方法 | 样本数 | Hit@10 | Recall@10 | MRR | NDCG@10 | 引用页命中率 |
|---|---:|---:|---:|---:|---:|---:|
| BM25 全库 | 114 | 24.56% | 24.56% | 0.1174 | 0.1476 | 32.46% |
| BM25 公司/报告期约束 | 114 | 62.28% | 61.62% | 0.3914 | 0.4443 | 66.67% |
| BM25 公司/报告期约束 + 中文别名 | 114 | **64.04%** | **63.38%** | **0.3963** | **0.4522** | **68.42%** |

这轮结果说明，公司和报告期约束是当前 CFQA 检索的主要收益来源；中文金融术语别名在此批次上继续带来小幅改善。结果只评价页级证据召回，不等同于答案正确率、金额正确率或投资建议质量。BGE 重排应在同一个 114 条分母和同一个候选池上做后续对照，不能和这轮 BM25 结果混报。

### 当前人工复核边界

本批次已自动完成来源、报告期、PDF 哈希和页级 Evidence ID 的生成，但仍需人工逐条确认：CFQA 页码是否对应 PDF 的打印页码、答案金额和单位是否一致、表格年份列是否正确、计算题的分子分母是否完整。未完成复核前，6 条未物化记录必须保持在 unresolved 队列中，不能通过填充相邻页或其他年份报告来扩大分母。

### 扩展批次复现命令

```powershell
python -m evaluation.resolve_cfqa_sources `
  --input runtime/external/cfqa_test_mapping_120.jsonl `
  --output runtime/external/cfqa_v1_120_candidates_resolved.jsonl `
  --sources-out runtime/external/cfqa_v1_120_sources_resolved.json

python -m evaluation.download_corpus `
  --sources runtime/external/cfqa_v1_120_sources_resolved.json `
  --target-dir runtime/external/cfqa_v1_120_pdfs `
  --snapshot-out runtime/external/cfqa_v1_120_snapshot.json `
  --reuse-existing

python -m evaluation.build_pdf_corpus `
  --lock runtime/external/cfqa_v1_120_snapshot.json `
  --download-dir runtime/external/cfqa_v1_120_pdfs `
  --source-manifest runtime/external/cfqa_v1_120_sources_resolved.json `
  --chunks-out runtime/reports/cfqa-v1-120.chunks.jsonl `
  --metadata-out runtime/reports/cfqa-v1-120.corpus.json

$meta = Get-Content runtime/reports/cfqa-v1-120.corpus.json -Raw | ConvertFrom-Json
python -m evaluation.materialize_cfqa_gold `
  --candidates runtime/external/cfqa_v1_120_candidates_resolved.jsonl `
  --chunks runtime/reports/cfqa-v1-120.chunks.jsonl `
  --corpus-version $meta.candidate_index_snapshot `
  --output runtime/reports/cfqa-v1-120.rag.jsonl `
  --unresolved-output runtime/reports/cfqa-v1-120.unresolved.jsonl

python -m evaluation.run_candidate_rag_eval `
  --cases runtime/reports/cfqa-v1-120.rag.jsonl `
  --chunks runtime/reports/cfqa-v1-120.chunks.jsonl `
  --source-manifest runtime/external/cfqa_v1_120_sources_resolved.json `
  --k 10 `
  --methods bm25_global bm25_entity_period_scoped bm25_entity_period_scoped_alias `
  --dataset-tier external_cfqa_candidate_pending_independent_review `
  --out runtime/reports/cfqa-v1-120.bm25.json
```

评测下载器支持 `--reuse-existing`：只有已存在且以 `%PDF` 开头的完整文件才会复用，损坏或 `.part` 文件仍会重新下载，避免重复运行时浪费网络和时间，同时不改变数据快照的哈希记录。

更新时间：2026-08-17

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
| CFQA v2 规范化评测副本 | 20 条、19 份年报、8,972 个文档块 | 页码修复、金额/单位/计算标签已写入派生副本；不覆盖原始数据，仍是 candidate | 证据值覆盖、计算分子/分母覆盖、答案和引用诊断 |

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

端到端评测现在已经可以对已规范化的数值题运行确定性判定，但仍要区分两种结果：
`evidence_pack` 只把 Top-k 证据打包，用来测“证据是否已被检索到”；它不是模型答案质量。
`configured_llm` 才是实际回答生成路径，后续应使用人工或稳定的模型裁判评估答案。
当前规范化副本的 BM25 公司/报告期约束诊断（`k=10`）为：直接事实值覆盖率
`12/13=92.31%`，计算题分子/分母证据覆盖率 `1/2=50%`。这两个数字比单独的
`answer_accuracy` 更适合定位当前问题：002 已找到 2520 和 3109，但仍需回答器计算
`-18.94%`；013 的资产负债率需要重新召回 p18 的两条证据。

本轮页面视觉核对记录见：[CFQA_V2_VISUAL_REVIEW.md](CFQA_V2_VISUAL_REVIEW.md)。
其中 17 条支持保留当前页，004、006、013 需要修复证据页；这份记录仍是
`pending_independent_human_review`，不能直接作为正式 Gold。

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

### 页码修复旁路对照

为量化标签质量的影响，另以视觉核对建议修复 004、006、013 的副本运行了同一套
BM25 检索。该副本明确标记为 `visual_repaired_pending_independent_review`，不替换
上面的主结果：

| 方法 | 原始副本 Hit@10 | 修复旁路 Hit@10 | 原始 Recall@10 | 修复旁路 Recall@10 | 修复旁路引用页命中率 |
|---|---:|---:|---:|---:|---:|
| BM25 全库 | 35.00% | 40.00% | 35.00% | 40.00% | 45.00% |
| BM25 公司/报告期约束 | 55.00% | 60.00% | 51.25% | 56.25% | 60.00% |
| BM25 公司/报告期约束 + 中文别名 | 65.00% | **70.00%** | 61.25% | **66.25%** | **70.00%** |

修复旁路只能说明页级标注会改变检索指标，不能证明模型能力已经提升；待独立人工
审核确认后，才可以重新生成正式评测报告。

旁路副本可由机器可读的修复清单重建：

```powershell
python -m evaluation.apply_cfqa_review `
  --cases runtime/reports/cfqa-v1-20.rag.jsonl `
  --chunks runtime/reports/cfqa-v1-20.chunks.jsonl `
  --manifest evaluation/datasets/cfqa_v2_visual_repairs.json `
  --out runtime/reports/cfqa-v1-20.rag.visual-repaired.jsonl
```

### 数值、金额和计算标签规范化

在视觉复核之后，规范化清单还会把已经核对过的答案事实写入派生副本：

```powershell
python -m evaluation.apply_cfqa_review `
  --cases runtime/reports/cfqa-v1-20.rag.jsonl `
  --chunks runtime/reports/cfqa-v1-20.chunks.jsonl `
  --manifest evaluation/datasets/cfqa_v2_visual_repairs.json `
  --out runtime/reports/cfqa-v1-20.rag.normalized.jsonl

python -m evaluation.frozen_dataset `
  --kind rag `
  --tier candidate `
  --dataset runtime/reports/cfqa-v1-20.rag.normalized.jsonl
```

当前规范化内容包括：002 的外购煤数量及变化率公式、004 的中标价和采购量单位、
006 的存货金额、013 的资产负债率分子/分母，以及其他已视觉核对的金额和比例。
原始 `cfqa-v1-20.rag.jsonl` 永远保留；规范化副本带有
`normalized_pending_independent_human_review` 标记，不提升为正式 Gold。

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
  --cases runtime/reports/cfqa-v1-20.rag.normalized.jsonl `
  --chunks runtime/reports/cfqa-v1-20.chunks.jsonl `
  --source-manifest runtime/external/cfqa_v1_sources_resolved.json `
  --k 10 `
  --retriever bm25_entity_period_scoped `
  --generator evidence_pack `
  --judge deterministic `
  --out runtime/reports/cfqa-v1-20.e2e.normalized.bm25.evidence-pack.json
```

`evidence_pack` 是证据覆盖诊断基线；它会把 Top-k 片段全部放入答案并引用对应页码，
因此不能把它的 `answer_accuracy` 当成大模型回答准确率。重点查看报告中的
`direct_fact_value_support_rate` 和 `calculation_operand_support_rate`。实际模型回答
应使用 `--generator configured_llm`，并另行审核答案、单位、公式和引用。

`runtime/external` 和 `runtime/reports` 是运行时目录，不把外部数据和生成报告
直接提交到代码仓库；依赖的提交号、源文件哈希、映射状态和评测协议保存在本文档
及数据集说明中。

## 下一步

1. 完成规范化副本中 20 条候选的金额、单位、正负号和计算式复核。
2. 从剩余 100 条映射队列中继续扩展不同公司和报告年份的官方年报。
3. 为新增数值题统一补充分子、分母、单位、证据页和计算过程标签。
4. 先用 BM25、证据覆盖诊断和确定性数值判定建立稳定基线，再对比 BGE；若 BGE
   继续重排，先把 BM25 候选池扩大到 Top-50/Top-100，并加入表格结构和
   公司/报告期保护规则，避免正确证据被无约束重排挤出。
