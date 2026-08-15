# Dynamic ephemeral subagents

Status: implemented

## Problem

The parent agent could dynamically choose among four fixed specialist roles,
but could not create a request-specific review role. Direct peer-to-peer agent
chat or arbitrary runtime role generation would make financial evidence,
permissions and lifecycle difficult to audit.

## Scope

Add one request-scoped, one-shot subagent instance that the parent may create
after evidence is available. The parent selects only a code-reviewed template
and provides a bounded objective. The instance reads compact parent
observations, produces a typed review result, then is destroyed.

## Contract

```json
{
  "action": "create_subagent",
  "template": "evidence-critic | risk-reviewer",
  "objective": "short evidence-review goal",
  "reason": "why a separate review is needed"
}
```

The parent trace must contain, in order for a successful run:

1. `ephemeral_subagent_created` with generated instance ID and template;
2. `ephemeral_subagent_result` with the persisted result reference;
3. `ephemeral_subagent_destroyed` with the same instance ID.

## Safety constraints

1. A dynamic instance is built from `EPHEMERAL_SUBAGENT_TEMPLATES`; the model
   cannot supply code, an arbitrary system prompt, a runner, permissions or
   tools.
2. It has zero tools and zero write/publish/trade permissions.
3. It receives at most eight compact, already approved parent observations;
   it never receives the mutable parent state or an unrestricted transcript.
4. Only one instance can be created per parent run and it requires prior
   observations.
5. It cannot call another subagent or persist itself beyond the current step.
6. Dynamic creation remains a logical in-process child run, not an OS process,
   container, broker action or external-service sandbox.

## Non-goals

- Peer-to-peer subagent messaging.
- Dynamic code execution or user-defined tool grants.
- Automatic broker execution, publication or persistent autonomous workers.
