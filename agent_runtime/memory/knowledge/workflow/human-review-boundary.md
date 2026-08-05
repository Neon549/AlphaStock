---
status: approved
scope: workflow
evidence_class: operating_knowledge
market_fact_policy: never_override_current_evidence
owner: human-reviewed
version: 1.0.0
reviewed_at: 2026-08-05
---

# 人工审核边界

投研系统可以自动形成带证据的研究草案，但不能自动发布为交易指令。无证据输出、
数据过期、来源冲突、工具错误和收益承诺都必须进入 requires_human_review。

## 审核输入

审核人应看到结论、关键指标、evidence ID、数据时间和未解决风险；原始工具结果
可按 evidence ID 回查。
