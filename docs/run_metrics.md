# Runtime metrics contract

Every completed Agent run produces `run_metrics/v2`. The same completed
object is persisted with the run and attached to the Langfuse root trace.
Langfuse is a consumer of the contract, not the source of truth for release
gates: an observability outage must not make a release report irreproducible.

The operational fields include elapsed time, peak in-process concurrency,
provider/tool failure, retry attempt and recovery, fallback use, token usage,
and estimated model cost. Retrieval, citation and execution status remain in
the same summary for run-level diagnosis.

## Versioned pricing

`config/model_pricing.py` contains the built-in USD-per-million-token snapshot
and writes its version into every run. Unknown models and successful calls
without usage data set `cost_estimation_complete=false`; the operational SLO
evaluator fails closed instead of treating them as free.

Deployments can supply additional USD prices without editing code:

```dotenv
ALPHASTOCK_MODEL_PRICING_VERSION=finance-approved-2026-08-28
ALPHASTOCK_MODEL_PRICING_JSON={"qwen3.7-max":{"input":0,"cache_hit":0,"output":0}}
```

Replace the example zero values with the deployment's contracted USD rates.
The required keys are `input`, `cache_hit`, and `output`.

## Langfuse

The root `alphastock/run` trace receives a `trace_summary` containing the same
`run_metrics/v2` object. It does not receive prompts, document contents,
evidence payloads, or credentials. PostgreSQL remains the durable audit source;
Langfuse can be used for dashboards, drill-down, and online sampling.
