# FinancialAgent-E2E 生产级准入 v1

## 目标

把已经脱敏的真实请求，从 intake、双人独立复核、争议仲裁到四次受控环境真实运行，连成可审计的准入链路；严禁把候选样例或未跑满的结果称为生产级评测。

## 准入规则

1. Case 只能来自 `deidentified_session` 或 `production_bad_case`，且必须有不可逆 `source_fingerprint`、当前脱敏版本、文档/工具快照哈希。
2. 两名不同 reviewer 必须对批准结论、rubric、允许证据和失败标签一致；不一致必须由 `arbitrator` 明确裁定。
3. 每个 `case × variant` 至少四条、唯一的真实运行记录；每条记录含执行时间、runtime 快照哈希与 trace 脱敏版本。
4. 运行记录不得包含 session、用户、设备、IP、原始轨迹等身份/原始字段。

## 指标解释

- `dataset_admission_ready`：来源、复核与四次运行完整，可以作为生产级 E2E 数据集报告失败率和稳定性。
- `release_gate_passed`：在上述前提上，所有关键 rubric 通过，高风险任务的安全 rubric 在所有重复运行中通过。
- 两者不能互相替代：失败样本必须保留在数据集中，不能为了发布指标而删除。

## 命令

```powershell
python -m evaluation.financial_agent_e2e_production_admission `
  --cases evaluation/datasets/financial_agent_e2e_real_review_queue_v1.jsonl `
  --reviews secure-export/reviews.jsonl `
  --runs secure-export/controlled_runs.jsonl `
  --out runtime/reports/financial_agent_e2e_production_admission.json `
  --required-runs 4
```
