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
| CFQA 页码映射抽样队列 | 120 | 固定随机种子 `20260813`；等待逐份定位官方年报 | 映射进度，不是 RAG Gold |
| CFQA 页锚定检索子集 | 9 条、7 份年报、3,319 个文档块 | 证据 ID 已生成；独立人工复核仍待完成 | 候选 Recall@10、MRR、NDCG、引用命中率 |

2,036 条记录不是 2,036 条已经可直接评估的 RAG Gold。CFQA 给出了答案页码，
但 AlphaStock 仍必须下载匹配的官方年报、固定文件哈希和 PDF 页序，再把页码映射
到 Evidence ID；完成独立复核后才能提升为正式 Gold。

## 检索实验

实验对象是 9 条页锚定候选，`k=10`，标签完整性检查通过。结果如下：

| 方法 | 命中率/Recall@10 | MRR | NDCG@10 | 引用页命中率 |
|---|---:|---:|---:|---:|
| BM25 全库 | 22.22% | 0.2222 | 0.2222 | 33.33% |
| BM25 公司/报告期约束 | 55.56% | 0.3603 | 0.4025 | 55.56% |
| BM25 公司/报告期约束 + 中文别名 | **77.78%** | **0.4317** | **0.5096** | **77.78%** |
| 中文向量 `shibing624/text2vec-base-chinese` | 0.00% | 0.0000 | 0.0000 | 22.22% |
| 中文向量 + RRF 混合 | 33.33% | 0.0639 | 0.1259 | 55.56% |
| BGE Cross-Encoder 重排 `BAAI/bge-reranker-v2-m3` | 33.33%* | — | — | — |

\* BGE 这一行来自端到端离线诊断的 `retrieval_hit_rate_at_k`，不是同一
候选评测器生成的 Recall 表格；它使用 BM25 候选池后再做 Cross-Encoder 重排。
因此只能作为当前小样本 A/B 信号，不能和前五行当作完全同协议的最终比较。

### 对结果的解释

当前最有效的是公司/报告期约束加金融术语别名扩展，说明本项目的主要瓶颈仍然
包括实体识别、报告定位和中文财务术语归一化。BGE 在这 9 条候选上没有提升，
原因可能是候选池太小、表格结构没有显式编码、页级证据和自然语言问题之间存在
格式差异；这不等于 BGE 在完整 CFQA 上无效。

这次端到端脚本返回 `judged_cases=0`。原因是当前 CFQA 页锚定候选尚未完成
独立答案判定，不能据此报告 `answer_accuracy` 或 `grounded_answer_accuracy`。
当前报告只发布检索和引用页诊断，不发布答案正确率。

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

运行已完成页锚定子集的稀疏检索对照：

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

BGE 端到端离线诊断：

```powershell
$env:HF_HUB_OFFLINE = "1"
python -m evaluation.run_rag_e2e_eval `
  --cases evaluation/corpus/external_cfqa_v1/rag_validation_candidates.jsonl `
  --chunks runtime/reports/external-cfqa-v1.chunks.jsonl `
  --source-manifest evaluation/corpus/external_cfqa_v1/sources.json `
  --k 10 `
  --retriever bm25_entity_period_scoped_reranked `
  --generator extractive `
  --judge deterministic `
  --out runtime/reports/external-cfqa-v1.e2e.bge.deterministic.rerun.json
```

`runtime/external` 和 `runtime/reports` 是运行时目录，不把外部数据和生成报告
直接提交到代码仓库；依赖的提交号、源文件哈希、映射状态和评测协议保存在本文档
及数据集说明中。

## 下一步

1. 从 120 条映射队列中优先补齐更多不同公司和报告年份的官方年报。
2. 对每条页码同时复核页序、表格行列、单位和答案是否真的由该页支持。
3. 将页锚定规模扩大后，再比较 BM25、中文向量、Hybrid 和 BGE 重排。
4. 为数值题补充分子、分母、单位和计算过程标签，之后才运行答案正确率与拒答评测。
5. 若 BGE 继续重排，先把 BM25 候选池扩大到 Top-50/Top-100，并加入表格结构和
   公司/报告期保护规则，避免正确证据被无约束重排挤出。
