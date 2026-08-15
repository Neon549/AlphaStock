# 受约束 Query Rewrite 与 LLM Decomposition

状态：已实施

## 目标

在不让模型改写事实、猜测实体、决定权限或执行交易的前提下，提升金融检索的
实体/时间/同义词匹配能力，并为复杂的多实体、多跳研究请求提供受约束任务图。

## Query Rewrite

`rag/query_rewrite.py` 生成以下审计对象：

```json
{
  "original_query": "茅台最近调价了吗",
  "rewritten_query": "茅台最近调价了吗 贵州茅台 600519 提价 上调零售价 合同价调整",
  "rewrite_reason": ["entity_canonicalized_local_mapping", "recent_news_window_30d"],
  "filters": {"stock_code": "600519", "news_days": 30},
  "rewrite_source": "deterministic"
}
```

- 实体仅以本地股票字典或用户显式代码规范化；未知代码不补全名称。
- “最近/近期”映射为新闻 `news_days=30` 过滤；“2025 年年报”映射为
  `report_period=2025` 元数据。报告期不会被伪造为文本事实。
- 回购、利润、营收、调价等仅追加受控金融同义词，原始数字、年份与代码不替换。
- 检索使用改写 query；审计保留原始 query。遥测只存脱敏 hash/preview、改写原因
  与过滤字段，不记录未脱敏查询。
- 改写异常时检索仍可使用原始 query；改写结果永远不能作为回答事实。

## 受约束 LLM Decomposition

确定性规则仍处理简单的顺序、并行、回测、扫描、筛选和交易确认。仅在以下
复杂只读研究形态才调用 LLM：多实体比较、多跳原因分析、条件逻辑、短期下跌与
长期基本面联动。交易文字完全不触发该 LLM 路径。

LLM 只能输出 `investment_analysis` 或 `comparison`：

```json
{
  "tasks": [
    {"task_type": "comparison", "stock_codes": ["600519", "300750"],
     "focus": ["fundamental", "technical"], "depends_on": []}
  ]
}
```

系统在接纳前执行：JSON/schema 校验、最多三任务限制、任务白名单、focus 白名单、
本地股票字典校验、仅可复用原始问题已出现的代码、比较必须刚好两个代码、依赖
只能指向更早任务、去重和 DAG 校验。

无效 JSON、模型失败、虚构代码、越界依赖都返回原有确定性结果；例如多股票请求
继续澄清，绝不任意挑选第一只。`comparison` 暂无执行技能绑定，任务图会显式标为
`route_to_dedicated_endpoint`，而不是假装已完成。LLM 不能创建交易/发布任务，不能
授予权限，不能移除确认门。

## 验证与边界

新增单元测试覆盖实体/时间/同义词 rewrite、原始 query 保留、未知代码不规范化、
比较任务、虚构代码和循环依赖拒绝、Parser 集成回退。该机制验证的是契约安全性，
不是对 LLM 分解准确率或 RAG 提升的生产声明。

后续只有在冻结、独立人工复核的复杂真实请求集上，同时比较原始检索与改写检索的
Recall@K、Precision@K、引用命中与延迟/成本，才决定是否扩大 LLM decomposition 覆盖。

## v1 候选 A/B 诊断

在固定 88 条合成 public-filing Query Variant 上，Rewrite 相比项目既有 scoped BM25
+ alias baseline：Recall@10、Precision@10、F1@10、Citation hit 均不变，MRR +0.0006，
nDCG@10 +0.0005；逐题为 1 胜、0 负、87 平。第一版宽松同义词扩展曾有 1 胜 3 负，
因此收缩为只扩展口语词、跳过已精确的标准披露字段。

该结果只作为 candidate 回归门槛，不能替代独立人工复核真实 Query 的 A/B 结论。
