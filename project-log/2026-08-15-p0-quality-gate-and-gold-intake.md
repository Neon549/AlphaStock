# 2026-08-15：P0 发布质量门禁与中文 Gold intake

## 本次修改

### 1. 新增 `evaluation/release_quality_gate.py`

把发布判断从“代码回归通过”扩展为失败闭环的质量门禁，检查：

- 代码回归与治理回归；
- RAG、E2E 成功率、引用正确率相对 baseline 不下降；
- P95 延迟预算；
- 平均 token 与成本预算；
- 红队样本必须存在且高风险失败为 0。

缺少任何必填报告或数值时默认阻断。该结果不是生产质量声明。

### 2. 新增 `evaluation/production_gold_intake.py`

建立中文真实 Gold 的结构准入：

- 来源仅允许 `deidentified_session` / `production_bad_case`；
- 检查不可逆来源指纹、corpus hash、脱敏版本和采集时间；
- 强制 `train` / `validation` / `test` split；
- 覆盖事实查询、财报、新闻验证、多股比较、高风险、缺失信息、多轮上下文和复合任务分类；
- RAG 题必须记录答案事实、Evidence ID、页码、拒答条件；
- routing 题必须记录意图、槽位、任务和澄清/拒答条件；
- 支持 `--require-dual-review`，双 reviewer 不足时不能成为 production-ready。

这两个模块均为离线结构检查，不会生成或伪造真实生产数据。

## 验证

```powershell
python -B -m pytest -q tests/evaluation/test_release_quality_gate.py tests/evaluation/test_production_gold_intake.py
```

结果：`10 passed`。

随后补齐 E2E 稳定性输出（兼容原有字段）：`pass_at_4`、`pass_caret_4`、
最终任务成功率、平均/最大步数、显式工具调用成功率和重复工具调用率。
`tests/evaluation/test_financial_agent_e2e.py` 新增覆盖并通过 `8 passed`。

又新增可选 `trajectory` 合约：任务可以声明理想工具、参数、顺序、禁止工具、
澄清和拒答行为；运行报告会给出工具选择/参数/顺序准确率、冗余调用、禁止调用
和轨迹通过率。未声明理想轨迹的历史任务不会被强行评估。

新增 `evaluation/operational_slo.py` 聚合显式 telemetry：并发、P50/P95/P99、
Provider/工具失败率、重试恢复率、fallback、token 和成本。缺失字段、重复 run_id
或空数据集均失败；它只聚合受控记录，不主动压测线上环境。

新增 `evaluation/red_team_eval.py`：对已记录的高风险 adversarial case/run 做确定性
安全检查，覆盖提示注入、越权工具、确认绕过、PII、收益保证和过期数据，并输出
可直接喂给质量门禁的 `total_cases / high_risk_failures`。

## 当前边界与下一步

仓库仍没有可直接提交的脱敏真实导出，因此没有新增 production-tier 数据集，
也没有宣称线上指标提升。下一步把受控导出送入 Gold intake，完成两人复核、
仲裁（如有）、冻结 test 和四次真实运行；随后把生成的报告接到该质量门禁。
