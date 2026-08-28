# 论文实验矩阵 v0.1

## 固定实验条件

每一行实验必须固定并记录：

- 数据集 ID 和 tier；
- corpus snapshot SHA-256；
- task 数据 SHA-256；
- prompt 版本；
- 模型和模型配置；
- retriever、reranker 和 Top-K；
- 工具快照；
- 运行时间、token、成本和延迟；
- 评测器版本。

不同数据集或不同快照的结果不能直接混成一行比较。

## 变体定义

| 变体 | 检索 | 证据过滤 | 引用/拒答 | Output Gate | 目的 |
|---|---|---|---|---|---|
| V0 | 基础 BM25 或现有最小检索 | 关闭 | 关闭 | 关闭 | 最小 baseline |
| V1 | 实体/时间约束检索 | 开启 | 关闭 | 关闭 | 测量证据过滤 |
| V2 | V1 | 开启 | 开启 | 关闭 | 测量引用和拒答 |
| V3 | V2 + 完整证据管线 | 开启 | 开启 | 开启 | 主方法 |
| V4-a | V3 去掉实体过滤 | 关闭 | 开启 | 开启 | 实体门禁消融 |
| V4-b | V3 去掉时间过滤 | 开启 | 开启 | 开启 | 时点门禁消融 |
| V4-c | V3 去掉引用检查 | 开启 | 关闭 | 开启 | 引用约束消融 |
| V4-d | V3 去掉拒答 | 开启 | 部分开启 | 开启 | 拒答策略消融 |
| V4-e | V3 去掉 Output Gate | 开启 | 开启 | 关闭 | 输出治理消融 |
| V4-f | V3 + BGE | 开启 | 开启 | 开启 | reranker 作为独立变量 |

V0–V3 是主实验，V4 是机制消融。BGE 不作为论文唯一创新点。

## 评测指标

### 检索

`Recall@5/10/20`、`MRR`、`nDCG`、`citation_hit_rate`、`page_citation_accuracy`、`entity_violation_rate`、`stale_evidence_rate`。

### 答案

`answer_accuracy`、`grounded_answer_accuracy`、`unsupported_answer_rate`、`abstention_compliance`、`Faithfulness`、`Answer Relevancy`。

RAGAS 指标只能作为辅助指标，不能替代事实答案和引用正确性。

### Agent 轨迹

`final_task_success_rate`、`tool_selection_accuracy`、`parameter_accuracy`、`tool_failure_rate`、`retry_recovery_rate`、`redundant_call_rate`、`average_steps`、`pass_at_4`、`pass_caret_4`。

### 运行和安全

`P50/P95/P99 latency`、`input/output/total tokens`、`cost`、`prompt_injection_failure`、`privilege_escalation_failure`、`PII_leakage`、`high_risk_failure`。

## 第一轮实验顺序

1. 使用 FinanceBench external Gold 确认 V0 的检索和端到端报告能够稳定生成；
2. 在同一 corpus 和同一模型上跑 V1、V2、V3；
3. 只在 V3 稳定后跑 V4 消融；
4. 每个主要变体至少重复 3–4 次，记录均值和方差；
5. 最终测试集冻结后不得继续根据结果改规则。

## 第一轮成功标准

第一轮不是追求所有指标都上升，而是回答：

1. V3 是否提高 grounded answer accuracy 或 citation correctness；
2. V3 是否降低 unsupported-answer 和高风险失败；
3. V3 的延迟和成本代价是否可接受；
4. 哪个模块负责收益，哪个模块造成损失。
