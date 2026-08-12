# Agent learning loop

This module turns audited AlphaStock runs into human-governed learning
artifacts. It does not automatically train on financial outputs.

```text
AgentEvent -> AgentRunResult -> deterministic rubrics
                                |- passed + explicit capture opt-in
                                |      -> pending trajectory review
                                |- safe_blocked / failed
                                       -> badcase record

human review -> labelled SFT/DPO candidate -> JSONL export -> TechLens training
```

`agent_run_evaluations` stores route, evidence, publication and bounded-execution
rubrics. `agent_badcases` stores safe blocks and execution failures with stable
fingerprints. `agent_training_candidates` stores opt-in trajectories as
`pending_review`; it cannot become train data until a reviewer supplies a
preferred target.

Enable capture for a run with `AgentEvent.metadata["learning_capture"] = true`
or the server-level `AGENT_LEARNING_CAPTURE=true`. The per-event flag is the
recommended default because it makes data-capture intent auditable.

Review and label a trajectory:

```bash
python scripts/review_agent_learning_candidate.py trajectory-... \
  --reviewer reviewer@example.com --kind sft --chosen-file reviewed_answer.txt
```

For DPO, also provide `--rejected-file`. Export only approved records:

```bash
python scripts/export_agent_learning_dataset.py --kind sft --out data/agent_sft.jsonl --mark-exported
python scripts/export_agent_learning_dataset.py --kind dpo --out data/agent_dpo.jsonl --mark-exported
```

For the LLaMA-Factory Alpaca format used by TechLens, export a JSON array with
`instruction/input/output` (SFT) or `instruction/input/chosen/rejected` (DPO):

```bash
python scripts/export_agent_learning_dataset.py --kind sft \
  --format llamafactory-alpaca --out data/agent_sft.json
```

This is a separate Agent dataset and must be registered under a new
LLaMA-Factory dataset name. Do not mix it directly with TechLens' existing
technical-indicator JSON dataset: the output contracts are different.

The deterministic gate intentionally evaluates execution contracts, not
semantic truth of every claim. An independent, structured evidence verifier
may later add claim-to-evidence scoring before human review; it must not grant
publication authority.
