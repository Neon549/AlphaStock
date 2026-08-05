---
status: approved
scope: retrieval
evidence_class: operating_knowledge
market_fact_policy: never_override_current_evidence
owner: human-reviewed
version: 1.0.0
reviewed_at: 2026-08-05
---

# 文档引用与相邻块规则

检索命中以子块为最小单元，引用必须带文件名、章节路径、页码或 evidence ID。
为了补齐切分边界，每个命中最多补充一个相邻块；不能把整章无条件塞进上下文。

## 回链

页面、章节或版本缺失时，明确标记引用信息不完整，不能伪造页码或将该内容描述为
已验证的当前事实。
