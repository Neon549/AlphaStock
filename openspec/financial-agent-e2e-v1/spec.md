# FinancialAgent-E2E-v1：端到端金融 Agent 评测

状态：实施中。

## 目标

建立面向 AlphaStock 的端到端评测底座，评价完整任务轨迹而非只评价某个
检索结果或最终文本。每个任务同时冻结问题、文档快照、工具快照和原子
Rubric；运行记录必须保留工具、参数、重试、澄清、证据、最终答复与安全门禁。

## 非目标

- 不复刻通用生活服务 Benchmark 的大规模模拟环境。
- 不把模板生成任务、回归 fixture 或候选集写成真实线上成功率。
- 不让 LLM Judge 单独决定交易、发布或证据事实；高风险 Rubric 必须可由
  轨迹/状态进行确定性检查。

## 数据契约

每个任务必须具有 4–8 个原子 Rubric，并至少一个 `critical=true`。高风险任务
必须至少一个 `safety=true` Rubric。支持的 Rubric 包括：最终答案关键词、轨迹事件、
工具选择、工具参数、页码引用、澄清、任务图、发布状态、无越权副作用和失败恢复。

任务的 `fixture` 必须固定 `task_sha256`、`document_snapshot_sha256`、
`tool_snapshot_sha256`。运行记录以 `case_id + variant + run_id` 关联任务，记录
`trace`、`citations`、`final_answer`、`run_metrics` 与标准失败 taxonomy。

严格任务成功要求全部 critical Rubric 通过；常规任务默认要求全部 Rubric 通过。
高风险任务必须额外全部 safety Rubric 通过。Rubric 级结果保留，不能只存一个
pass/fail。

## 指标

对于每个任务/策略，至少运行四次，报告：

- `avg_success_rate`：四次运行成功比例；
- `pass_at_4`：至少一次成功；
- `pass_hat_4`：四次均成功；
- Rubric、critical Rubric 和 safety Rubric 通过率；
- P50/P95 延迟、平均成本、平均工具调用数；
- 需要恢复的运行中的恢复成功率；
- 按定义 taxonomy 聚合的失败次数和失败率。

## 任务覆盖与推广门槛

首批 candidate fixture 必须覆盖：单股事实、多来源研究、跨报告期、上下文指代、
缺失信息澄清、复合任务、高风险交易/发布、工具失败恢复。它仅验证评测框架。

生产级 E2E 集目标为 80–120 条，经脱敏、独立于训练/调参集、两名 reviewer 独立
标注与第三人仲裁后才能入库；每题需标明来源、审核人、审核日期、允许证据、失败
类型和 rubric。只有该层数据才可用于生产或简历效果声明。
