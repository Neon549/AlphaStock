# 固定快照消融协议

每个实验行必须固定同一任务、同一文档快照和同一工具快照，并记录其 SHA-256：

```json
{
  "fixture_id": "600519-technical-v1",
  "fixture": {
    "task_sha256": "...",
    "document_snapshot_sha256": "...",
    "tool_snapshot_sha256": "..."
  },
  "variant": "deepseek-python-runtime",
  "publish_status": "requires_human_review",
  "run_metrics": {"elapsed_ms": 0, "input_tokens": 0, "tool_call_count": 0},
  "run_telemetry": {"llm_calls": []}
}
```

价格文件按每 token 提供输入、缓存输入和输出单价。运行：

```powershell
python evaluation/ablation_report.py --runs runtime/reports/ablation-runs.jsonl --prices evaluation/fixtures/model_prices.json --output runtime/reports/ablation-report.json
```

报告输出各变体的成本、P50/P95、平均输入 Token、平均工具调用数和质量门禁通过率。若工具或文档未冻结，报告器会拒绝该行。
