# RAG Recall 提升记录

这份记录只收录会影响检索 Recall、Precision 或端到端语义评估的较大改动。
每次记录包含基线、改动、验证方式、结果和 Git commit，避免把不同语料快照的指标混在一起。

## 指标口径

- `Recall@K`：候选证据是否覆盖预先定义的答案证据，属于检索层指标。
- `Context Recall / Context Precision`：RAGAS 对回答、参考答案和上下文的语义评估，不等价于端到端答案准确率。
- 所有远程数据库结果必须注明 corpus snapshot 的行数、股票数和时间范围。

## RAG-R1：加权 Hybrid 接入线上链路

- 日期：2026-08-13
- commit：`f5e1cfe`
- 改动：中文金融同义词扩展；数字、股票代码和中文字符增强分词；候选池至少 20 条；BM25 2× 加权 RRF；RAGAS 使用 DashScope `text-embedding-v3`，Answer Relevancy 采用兼容当前模型 API 的 `n=1`。
- 验证：项目测试 `228/228` 通过；10 条远程 DB 样本的 RAGAS 消融。
- 结果：最佳组合 `hybrid_scoped_bm25_2x` 的 Context Recall `0.6000`、Context Precision `0.6224`、Faithfulness `0.9347`；BM25 scoped 的 Context Precision `0.6905`。
- 结论：排序优化有效，但部分答案事实根本不在当前新闻库，下一步必须补充语料。

## RAG-R2：评测股票纳入生产刷新列表（进行中）

- 日期：2026-08-13
- 改动：将 10 条评测股票统一纳入 `EVAL_STOCKS`，并与生产 `WATCH_LIST` 合并去重；新增 `scripts/refresh_news_index.py`，支持通过 SSH 隧道增量刷新、指定股票、刷新全部已有股票和 dry-run。
- 刷新前快照：远程 `news_vectors` 约 `1,370` 条、约 `171` 只股票；最近 7 天约 `127` 条；部分评测股票最新日期停在 2026-08-07 至 2026-08-11。
- 运行结果：待刷新完成后补充新增条数、快照和 Recall/RAGAS 对比。
- 预期：优先补齐 601138、002415、300124、600487 等评测股票的最新新闻，减少“答案事实不在库”的缺口。
