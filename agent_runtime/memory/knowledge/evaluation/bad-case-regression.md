---
status: approved
scope: evaluation
evidence_class: operating_knowledge
market_fact_policy: never_override_current_evidence
owner: human-reviewed
version: 1.0.0
reviewed_at: 2026-08-05
---

# Bad Case 回归处理

线上 Bad Case、人工复盘和回测偏差先作为 pending 候选保存原始 run ID、归因和
复现步骤；通过人工审核后才写入长期经验库并显式触发索引同步。

## 发布门禁

不可变治理用例必须全部通过；检索、模型或 prompt 的改动不能使固定验证集指标
低于已记录基线。候选记忆不能自行批准或覆盖实时市场证据。
