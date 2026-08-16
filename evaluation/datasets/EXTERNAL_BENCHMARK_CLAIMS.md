# 外部基准声明边界

## 中文主基准：CFQA

AlphaStock 的中文年报问答主基准是 CFQA。当前固定的公司划分测试文件包含
2,100 条原始记录，过滤空问题和空答案后有 2,036 条可导入候选；其中 9 条已经
完成官方年报页锚定并生成 Evidence ID，但仍等待独立人工复核。

可以这样描述当前工作：

> 搭建中文上市公司年报问答的页码可追溯 RAG 评测链路，固定 CFQA 源仓库提交和
> 测试文件哈希，完成 2,036 条候选导入，并在 9 条页锚定候选上比较 BM25、中文
> 向量、Hybrid 和 BGE 重排的检索与引用页命中表现。

不能这样描述：

- 不能把 2,036 条待映射候选称为人工 Gold；
- 不能把 9 条待独立复核候选称为生产准确率；
- 不能把 Recall@10、引用页命中率或 RAGAS 分数称为答案正确率；
- 不能把公开 CFQA 题目称为 AlphaStock 线上用户日志。

详细版本、命令和结果见 [`CFQA_RAG_REPORT.md`](CFQA_RAG_REPORT.md)。

## 可选对照：FinanceBench

FinanceBench 仅保留为英文 SEC 文件场景的跨市场对照，不替代 CFQA，也不参与中文
主结论。其公开样本、文件页码和人工答案可以支持明确标注协议的外部基准报告，
但仍然不是 AlphaStock 线上流量、生产质量或投资收益率。

运行命令：

```powershell
python -m evaluation.import_financebench
python -m evaluation.run_financebench_eval --out runtime/reports/financebench-v1.retrieval.json
```

如果报告 FinanceBench 数字，必须同时写清楚“FinanceBench 英文跨市场对照”、
样本数量、检索协议和是否使用自动答案判定器。不得把它简称为 AlphaStock 中文
RAG 准确率。
