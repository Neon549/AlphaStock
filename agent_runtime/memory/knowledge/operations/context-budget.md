---
status: approved
scope: operations
evidence_class: operating_knowledge
market_fact_policy: never_override_current_evidence
owner: human-reviewed
version: 1.0.0
reviewed_at: 2026-08-05
---

# 上下文预算与工具结果

工具原始输出不能无限累积到 prompt。先保留来源、时间、新鲜度、关键字段和
result_ref；大结果放到可回查存储，向模型只注入受预算限制的预览。

## 压缩原则

当上下文接近预算时，优先淘汰低优先级历史和重复工具输出；当前任务、最新证据、
结构化状态和未解决风险优先保留。压缩不等于重新检索。
