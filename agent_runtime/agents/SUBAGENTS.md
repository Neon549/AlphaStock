# Bounded Investment Subagents

## Scope

The investment parent planner may dynamically delegate research work, but it
may select only a name registered in `subagents.py`.  A child is a bounded,
logical execution with an isolated `SubagentTask`; it is not a free-running
agent, a publishing authority, or an OS-level sandbox.

```text
Parent planner -> allowlisted subagent(s) -> typed SubagentResult -> parent harness
                                                               -> validation/output gate/HITL
```

## Registered roles

| Name | Purpose | Permissions | Output |
|---|---|---|---|
| `technical-researcher` | Price, volume and deterministic indicators | `market:read` | `technical_report` |
| `fundamental-researcher` | Financial indicators and already-retrieved document evidence | `market:read` | `fundamental_report` |
| `sentiment-researcher` | Current news and market sentiment | `market:read` | `sentiment_report` |
| `evidence-reviewer` | Retrieve page-linked session-document evidence | `document:read` | `user_doc_context`, citations |

## Planner contract

The planner may emit one bounded batch:

```json
{
  "action": "subagents",
  "subagents": ["technical-researcher", "sentiment-researcher"],
  "reason": "Need independent price and news evidence."
}
```

`evidence-reviewer` is intentionally sequential: it must finish before a
specialist consumes the retrieved document evidence.  The registry allows at
most three parallel child roles in one parent step.

## Safety invariants

1. The model chooses from names already exposed by the registry; it cannot
   create a role or add permissions at runtime.
2. Child tasks carry only stock code, request text, optional session ID and
   document evidence—not the parent session transcript or mutable state.
3. The parent harness checks permissions, records `subagent_result` traces,
   persists result references, applies validation, and owns fallback behavior.
4. No subagent may trade, publish, approve a recommendation, alter Memory, or
   bypass `Output Gate -> Human-in-the-loop`.
5. Existing `analysis` Skill remains a compatibility and safe-fallback path;
   new planner prompts prefer explicit specialist subagents.

## Current boundary

The first version isolates logical context and capability declarations inside
one Python service.  It does not yet isolate children in separate processes or
containers.  If arbitrary shell, write, or remote MCP capabilities are added,
they must first gain OS/container and per-tool network policies in the
Harness; a registry declaration alone is not a sandbox.
