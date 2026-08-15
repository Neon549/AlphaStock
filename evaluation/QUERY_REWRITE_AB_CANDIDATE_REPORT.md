# Query Rewrite A/B 候选诊断（v2）

## 评测协议

- 固定语料、gold label、候选范围、BM25 参数与 `Top-K=10`；不调用 LLM。
- Baseline：现有 entity/period scoped BM25 与 filing alias expansion。
- Treatment：在相同 baseline 前增加 `rewrite_retrieval_query()`；仅允许本地股票映射、时间过滤、受控金融同义词追加。
- 原始 Query 仍用于审计；改写文本只用于检索，不能成为回答事实。

## 通用候选集：88 条

`rag_query_variants.jsonl` 是模板生成的 candidate，而非真实用户流量。结果如下：

| 指标 | Baseline | Rewrite | Delta |
|---|---:|---:|---:|
| Recall@10 | 0.6375 | 0.6375 | 0.0000 |
| Precision@10 | 0.0638 | 0.0638 | 0.0000 |
| MRR | 0.2454 | 0.2460 | +0.0006 |
| nDCG@10 | 0.3384 | 0.3389 | +0.0005 |
| Citation hit rate | 0.6875 | 0.6875 | 0.0000 |

逐题为 1 胜、0 负、87 平。结论是通用样本上通过了非退化门槛；它没有证明整体 Recall 已提升。

## 信息缺口压力集：16 条

新增 `rag_query_rewrite_stress_candidate_v1.jsonl`，从同一冻结 public-filing candidate label 以确定性模板生成“简称 + 口语金融字段”问法（例如“茅台 2024 年营收”“平银 2025 年息差”）。它特意衡量普通全集稀释掉的困难分桶。

| 指标 | Baseline | Rewrite | Delta |
|---|---:|---:|---:|
| Recall@10 | 0.0625 | 0.3750 | +0.3125 |
| Precision@10 | 0.0063 | 0.0375 | +0.0312 |
| MRR | 0.0125 | 0.0929 | +0.0804 |
| nDCG@10 | 0.0242 | 0.1592 | +0.1350 |
| Citation hit rate | 0.0625 | 0.4375 | +0.3750 |

逐题为 6 胜、0 负、10 平。提升来自受审计的本地别名映射（如“茅台→贵州茅台/600519”）和披露术语归一化（如“息差→净息差”），不是 LLM 猜实体。

## 实现边界

- 使用很小、版本控制的高频别名表；泛化后缀如“集团”“科技”不解析，以避免误绑实体。
- 支持 `context_stock_code`：无实体的“这家公司”可继承已验证上下文；用户显式 ticker 优先于上下文。
- 复杂多来源问题仍交给既有受约束任务分解；本改写器不制造来源、不改写数字/年份/代码，也不直接回答。

## 结论与下一步

16 条压力集为合成 candidate，且与原标签同源，不能表示真实用户分布、独立人工复核结果、生产 KPI 或简历指标。下一步应在脱敏真实复杂 Query 上冻结测试集，由两名独立 reviewer 标注证据、页码、可拒答性和偏好，再报告分桶 Recall/Precision/Citation、延迟与成本。
