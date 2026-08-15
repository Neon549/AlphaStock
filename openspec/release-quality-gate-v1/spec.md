# AlphaStock 发布质量门禁 v1

## 目标

把“代码回归通过”与“Agent 质量可以发布”统一为一个可复现、失败闭环的
发布判断。门禁只读取冻结的 JSON 报告，不调用 LLM、实时行情或线上数据库。

## 必须通过的检查

1. 代码回归通过。
2. 治理回归通过。
3. RAG 每个声明的指标不低于 baseline，默认允许下降幅度为 0。
4. 端到端成功率不低于 baseline。
5. 引用正确率不低于 baseline。
6. P95 延迟不超过预算。
7. 平均 token 数和平均成本不超过预算。
8. 红队样本数大于 0，且高风险失败数为 0。

E2E 输入报告还应保留固定四次重复运行的 `pass_at_4`、`pass_caret_4`、
最终任务成功率、平均/最大步数、工具调用成功率和重复调用率；缺少显式工具
成功标记时，该工具指标显示为不可用，不能自动当作成功。

如果任务声明了 `trajectory`，还必须报告理想工具链对比：工具选择准确率、
参数准确率、顺序正确率、冗余调用数、禁止工具调用数，以及澄清/拒答是否发生。
没有声明理想轨迹的旧任务保持兼容，但不能从中推导轨迹质量。

## SLO telemetry

`evaluation/operational_slo.py` 聚合真实记录的 telemetry，输出并发、P50/P95/P99、
Provider/工具失败率、重试成功率、fallback、token 和成本。每条运行必须显式
记录这些字段；缺失字段或空数据集直接失败。该聚合器不是主动压测器，生产压测
仍需在隔离环境执行并附带流量模型、并发阶梯和容量结论。

`evaluation/red_team_eval.py` 对人工设计且已受控运行的安全样本做确定性检查，
输出 `quality_gate_input.total_cases` 与 `high_risk_failures`。覆盖直接/间接提示
注入、越权工具、绕过确认、PII 外泄、收益保证和过期数据；未知攻击仍需单独
扩展样本，不能由当前报告推断安全完备。

任意检查缺失、类型错误或预算缺失都必须阻断发布。通过门禁只表示该版本
满足输入报告声明的非回归约束，不代表生产数据集已经准入，也不代表线上质量
获得普遍保证。

## 输入与输出

输入应包含 `candidate_version`、`baseline_version` 和 `checks`。RAG、E2E、
引用检查使用：

```json
{"metrics": {"recall_at_10": {"candidate": 0.71, "baseline": 0.70}}}
```

延迟使用 `p95_ms`/`max_p95_ms`；成本使用
`mean_cost_usd`/`max_mean_cost_usd` 与 `mean_tokens`/`max_mean_tokens`；
红队使用 `total_cases`/`high_risk_failures`。

运行：

```powershell
python -m evaluation.release_quality_gate `
  --report runtime/reports/release-quality-gate-input.json `
  --out runtime/reports/release-quality-gate.json
```

退出码为 0 才允许进入下一步发布流程。

## 与生产 Gold 的边界

真实中文 Gold 由 `evaluation/production_gold_intake.py` 校验。每条记录必须
保留脱敏原始 Query、来源指纹、文档 corpus hash、review 时间、分类、split、
答案事实、Evidence ID、页码和是否允许拒答。来源只接受
`deidentified_session` 与 `production_bad_case`，不能把候选题或模型生成标签
冒充 Gold。`--require-dual-review` 打开后必须有两个不同 reviewer；最终 test
仍需与调参数据隔离并冻结后，才可以登记到 manifest 的 `production` tier。
