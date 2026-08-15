# Compound intent routing and evaluation

Status: implemented

## Problem

The existing four-class router expresses the primary execution boundary
(`discussion`, `investment_analysis`, `system_action`, `clarify`), while a
deterministic task graph can already hold more than one task.  It did not,
however, expose whether a request is a genuine compound request, why it is
compound, or whether its tasks must run sequentially, may run in parallel, or
are stopped at a confirmation boundary.  Earlier sequencing also assumed that
analysis always preceded a system task instead of reading user order.

## Scope

Keep the four top-level intent classes.  Add a backward-compatible
`compound_intent` metadata object to parser output; it is an orchestration
classification, not a fifth top-level fastText label.

The parser must classify a request as compound only when it contains at least
two distinct executable task intents.  Multiple analyst focuses in one
`investment_analysis` task (for example, technical + fundamental) remain a
single request with `analyst_focus=all`.

## Contract

Every parser result contains:

```json
{
  "compound_intent": {
    "detected": true,
    "classification": "sequential | parallel | confirmation_gated | single",
    "execution_policy": "sequential_stages | parallel_stage | confirmation_gate | single_task",
    "task_intents": ["investment_analysis", "backtest"],
    "source": "deterministic_orchestration"
  }
}
```

- `sequential`: explicit ordering language (`先…再…`, `然后`, `之后`, etc.)
  forms dependency edges in the order in which executable actions appear.
- `parallel`: distinct read-only actions without an ordering marker have no
  dependency edge and may share a DAG stage.
- `confirmation_gated`: any compound request containing `trade_action` keeps
  the trade task dependent on prior work and marks it confirmation-required.
  It is never passed to a broker/tool executor.
- `single`: zero or one executable action. `multi_intent` remains compatible
  and agrees with `compound_intent.detected` for parser-generated tasks.

## Safety constraints

1. Classification and graph edges are deterministic; an LLM may provide
   candidate stock slots but cannot invent a side-effect permission or graph
   dependency.
2. A trade request always retains `requires_confirmation=true`.
3. Missing stock codes still cause analysis/backtest tasks to be blocked by
   the task-DAG compiler; compound classification does not make them runnable.
4. The new metadata must not change the legacy `intent`, `stock_code`,
   `analyst_focus`, `sub_intents`, or `multi_intent` fields.

## Evaluation

Add a frozen smoke fixture with single, sequential, parallel, confirmation
gated, reverse-order and multi-focus negative cases.  The evaluator reports:

- binary compound detection precision, recall, and F1;
- compound classification exactness;
- execution-policy exactness;
- existing primary-intent, slot, and full task-graph exactness.

This is a seeded contract/smoke evaluation, not an online accuracy claim.

## Non-goals

- No fifth top-level fastText classifier label.
- No unconstrained natural-language planner or arbitrary workflow execution.
- No broker integration, automatic order placement, or claim of
  production-representative accuracy without independently reviewed traffic.
