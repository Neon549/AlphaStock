---
status: approved
scope: governance
evidence_class: operating_knowledge
market_fact_policy: never_override_current_evidence
owner: human-reviewed
version: 1.0.0
reviewed_at: 2026-08-03
---

# 事实证据与长期经验的边界

长期经验用于约束研究过程，例如检查数据是否过期、工具失败时如何降级、
以及何时必须要求人工审核。它不构成对任意股票当前价格、财务指标、公告或
新闻的事实证据。

## 当前事实必须单独验证

涉及公司、价格、财务数值、市场新闻或交易日状态的结论，必须由当次市场工具
或带页码的文档证据支持。经验库中的旧案例只能提醒 Agent 继续核验，不能替代
当前数据源，也不能被写成确定性投资建议。

## 无证据或证据冲突时的处理

当工具失败、数据日期无法确认、或不同来源发生冲突时，输出中应明确标记数据
不足或冲突原因，并保留来源 ID。不能为了给出完整答案而补造数值；需要行动的
结论应进入人工审核，而不是自动发布。
