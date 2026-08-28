# Health monitoring contract

AlphaStock separates process health from traffic admission and dependency
diagnostics. Monitoring and deployment automation should use these endpoints
instead of treating every HTTP 200 response as proof that the Agent is ready.

| Endpoint | Meaning | Success | Recommended consumer |
| --- | --- | --- | --- |
| `GET /health/live` | The API process can serve HTTP. It performs no dependency calls. | Always `200` while the process is alive. | Container/process liveness probe. |
| `GET /health/ready` | Required traffic-serving dependencies are ready. | `200` only when PostgreSQL, the business router, and primary model configuration are ready; otherwise `503`. | Load balancer, deployment smoke check. |
| `GET /health/dependencies` | Sanitized diagnostic snapshot. | `200` when PostgreSQL is reachable; `503` when it is not. | Dashboard and incident diagnosis. |
| `GET /health` | Legacy liveness alias. | `200`. | Compatibility only. |

`/health/dependencies` includes PostgreSQL query latency, pgvector availability,
business-router and news-index state, and whether primary/backup model and
Langfuse credentials are configured. It never returns DSNs, credential values,
provider response bodies, prompts, or documents.

Provider checks are passive by design. The health endpoint does not call a
model or Langfuse because doing so would consume quota and amplify an outage.
`configured_unverified` therefore means credentials are present but no active
network probe was made. Runtime failure and success rates belong in the
`run_metrics/v2` dashboard and alerts.

## Initial alerts

Start with symptom-based alerts, then tune thresholds from observed production
baselines:

- readiness remains `503` for 2 minutes;
- PostgreSQL probe failures for 2 consecutive checks;
- business router remains `initializing` for 2 minutes or becomes `failed`;
- `run_metrics/v2` provider or tool failure rate exceeds its rolling baseline;
- citation-validation failures or evidence abstention rates change sharply;
- cost estimation becomes incomplete (release evaluation already fails closed).

The backend deployment retries readiness for up to 90 seconds. If it never
passes, the workflow restores the exact previous Git revision, reinstalls that
revision's dependencies, restarts the service, and marks the deployment failed.
