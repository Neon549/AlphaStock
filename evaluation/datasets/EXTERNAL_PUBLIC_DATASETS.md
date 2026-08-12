# 外部公开金融数据集接入边界

外部数据用于补充 AlphaStock 的查询分布、路由压力和检索候选，不能直接表述为线上用户日志，也不能在没有独立复核时作为简历上的最终指标。

## 第一批：CFQA

CFQA 是中文上市公司年报问答数据。每条记录包含股票代码、公司、问题、答案和答案对应的 PDF 页码；页码按 PDF 文档顺序从 1 开始，不等同于报告底部印刷页码。仓库和许可证信息保存在导入记录的 `provenance` 中。

本地导入：

```powershell
$commit = (git -c safe.directory=runtime/external/CFQA -C runtime/external/CFQA rev-parse HEAD).Trim()
python -B evaluation/import_external_public_qa.py `
  --repo runtime/external/CFQA `
  --split test `
  --commit $commit `
  --output runtime/external/cfqa_test_candidates.jsonl
```

导入结果只是 `pdf_mapping_pending` 候选集。要用于 RAG Recall@K，需要：

1. 根据公司、股票代码和问题年份定位官方年报 PDF；
2. 固定 PDF 下载地址、文件哈希和 PDF 总页数；
3. 将 CFQA 的 PDF 页码映射到 MinerU/PyMuPDF 生成的 Evidence ID；
4. 人工复核问题、答案、页码和是否允许拒答；
5. 独立冻结后，才提升为 AlphaStock 的 `production` 测试集。

## 第二批：FinTruthQA

FinTruthQA 更适合做真实投资者问法、意图识别和回答质量压力集。它没有和 AlphaStock Evidence 一致的页码级证据标注，因此不能直接用于 RAG Recall@K；可单独评估意图、槽位、澄清率和答案相关性。

## 指标分层

- CFQA + 官方年报 + 独立页码复核：可用于中文年报检索 Recall/Precision/NDCG。
- FinTruthQA：可用于真实用户问法分布、意图/槽位和回答质量，不混入 Evidence Recall 主指标。
- AlphaStock 脱敏会话：只有在去标识、时间冻结、双人复核后，才可进入 production-tier 端到端评测。

外部公开数据的正确表述是“在公开金融 QA/年报语料上的外部评测”，不能写成“线上真实用户准确率”。
