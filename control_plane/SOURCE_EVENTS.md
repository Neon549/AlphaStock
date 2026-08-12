# Source-change events

新闻、财报和行情源不再需要“每天无条件重跑一次”。系统把定时任务降级为变化检测器；外部供应商也可以通过签名 Webhook 发送同一种事件。

```text
Source Registry
    -> Cron Watcher / signed Webhook
    -> version + content_hash comparison
    -> SourceIngestionWorker (MinerU/OCR + chunk + pgvector)
    -> AgentEvent(trigger=source_change)
    -> Gateway idempotency and audit
    -> InvestmentRuntime / InvestmentHarness
    -> focused research draft
    -> Output Gate / human review
```

## Source identity

Every source has a stable `source_id`, for example:

```text
cninfo:600519:annual-report
eastmoney:600519:news
akshare:600519:market-price
```

One revision is identified by:

```text
source_id + source_version + content_hash
```

The resulting `event_id` is the same for a Cron retry and a provider Webhook retry. A corrected document with the same version but a different hash creates a new event.

## Webhook payload

```json
{
  "type": "financial_report.changed",
  "source_id": "cninfo:600519:annual-report",
  "source_type": "financial_report",
  "source_version": "2025-annual",
  "content_hash": "sha256:...",
  "affected_symbols": ["600519"]
}
```

The Webhook signature is checked before this payload becomes an `AgentEvent`. Gateway does not parse the document or call an LLM; it only deduplicates, audits and dispatches. The runtime then restricts a source refresh to one affected stock per run and maps financial reports/news/market data to fundamental/sentiment/technical focus respectively.

## Two-phase ingestion

`SourceIngestionWorker` uses an injected `fetcher` and `ingestor`:

1. `fetcher` gets the provider payload and revision/hash.
2. `SourceRegistry.inspect` checks whether this revision is new without committing it.
3. `ingestor` parses and indexes the payload. For a PDF this is where the
   MinerU → OCR fallback → hierarchical chunks → pgvector pipeline runs.
4. Only after indexing succeeds does the worker persist/commit the revision
   and dispatch `AgentEvent(SOURCE_CHANGE)`.

If parsing or indexing fails, the revision remains retryable and no research
run is started. This prevents a failed ingestion from being recorded as a
successful daily update.

## Persistence

- `agent_sources` stores source registration and the last observed revision.
- `agent_source_changes` stores accepted revisions and provides a unique `dedupe_key` across process restarts.
- `agent_events`, `agent_runs` and `agent_steps` retain the normal control-plane audit trail.

The source update path is therefore event-driven, but it does not bypass the existing permission checks, evidence trace, Output Gate or human-review boundary.
