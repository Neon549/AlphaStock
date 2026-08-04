# Approved Agent Memory

Only Markdown files with this front matter are eligible for indexing:

```markdown
---
status: approved
scope: governance | research | retrieval | workflow | operations | backtest | evaluation
evidence_class: operating_knowledge
market_fact_policy: never_override_current_evidence
owner: human-reviewed
---
```

Store reusable, verified operating knowledge here: approved investigation
checklists, governance lessons, and post-mortems. Do not store live prices,
unreviewed model output, secrets, user chat logs, or investment promises.

Read [MEMORY_DATASET_SPEC.md](MEMORY_DATASET_SPEC.md) before expanding this
directory. Approval is a human decision: generated candidates are not source
documents and must never be bulk-promoted into this directory.
