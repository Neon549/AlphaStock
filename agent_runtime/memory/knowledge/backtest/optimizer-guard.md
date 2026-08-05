---
status: approved
scope: backtest
evidence_class: operating_knowledge
market_fact_policy: never_override_current_evidence
owner: human-reviewed
version: 1.0.0
reviewed_at: 2026-08-05
---

# 回测优化保护

回测数据加载或策略执行出现 TOOL_ERROR 时，只能返回失败原因和缺失数据说明，
不得继续用 mock 数据优化并伪装为完整结果。

## 解释边界

回测结果是历史样本的研究证据，不是未来收益承诺。优化参数必须与原始策略结果
分开呈现，并说明样本外验证、交易成本和过拟合风险。
