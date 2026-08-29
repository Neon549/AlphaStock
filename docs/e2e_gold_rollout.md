# FinancialAgent E2E Gold rollout

This workflow creates the first real, de-identified, independently reviewed
FinancialAgent Gold set without promoting synthetic fixtures or copying raw
production sessions into the repository.

## Data boundary

Export only already-redacted rows from the controlled production environment.
Do not place raw conversations, user IDs, trace IDs, account data, IP addresses,
email addresses, phone numbers, or raw provider payloads in this workspace.
`financial_agent_e2e_intake` is a rejection boundary, not a redaction service.

The recommended first batch is 80–120 cases covering factual research,
financial reports, news verification, multi-stock comparison, missing data,
multi-turn/compound requests, provider/tool failure recovery, prompt injection,
trade instructions, publication attempts, and guaranteed-return wording.

## Runbook

0. Open a time-bounded, approved capture window on the production host by
   setting `ALPHASTOCK_E2E_INTAKE_CAPTURE=true` in the service environment and
   restarting the API. This flag is off by default and retains raw prompt text
   only for the controlled collection period. Disable it immediately after the
   80--120 eligible runs are collected. Do not enable it in development or
   commit captured files to the repository.

1. On the production host, create a safe labeling worksheet. The export key is
   a secret stored only in that host's environment; it must never be committed
   or sent to the reviewers:

   ```powershell
   $env:ALPHASTOCK_EXPORT_FINGERPRINT_KEY = "<production-only-secret>"
   python scripts/export_e2e_intake.py `
     --label-template-out D:\secure-export\e2e-labels.jsonl `
     --collected-at 2026-09-30
   ```

   Transfer only this worksheet to the reviewers. It contains redacted query
   text and irreversible fingerprints, never raw transcript or run identity.
   Reviewers fill `category`, `risk_level`, `observed_failure_taxonomy` and
   `proposed_rubrics`; the script rejects missing or invalid labels.

2. Validate the completed controlled export and materialize review cases:

   ```powershell
   python scripts/export_e2e_intake.py `
     --labels D:\secure-export\e2e-labels.completed.jsonl `
     --document-snapshot sha256:<64-hex> `
     --tool-snapshot sha256:<64-hex> `
     --out D:\secure-export\e2e-intake.jsonl
   python -m evaluation.financial_agent_e2e_intake `
     --intake D:\secure-export\e2e-intake.jsonl `
     --review-cases-out runtime\gold\review-cases.unsplit.jsonl
   ```

3. Assign stable splits before anyone reviews or tunes against the cases:

   ```powershell
   python -m evaluation.financial_agent_e2e_split `
     --cases runtime\gold\review-cases.unsplit.jsonl `
     --dataset-id financial-agent-real-v1 `
     --out runtime\gold\review-cases.jsonl
   ```

   The v1 policy assigns 20% train, 20% validation and 60% untouched test by a
   deterministic hash. It refuses to reshuffle an already assigned dataset.

4. Generate separate worksheets. Reviewer A and B must work independently and
   must not see the other's decisions:

   ```powershell
   python -m evaluation.financial_agent_e2e_review_templates `
     --cases runtime\gold\review-cases.jsonl `
     --reviewer-a reviewer-a-pseudonym `
     --reviewer-b reviewer-b-pseudonym `
     --out-dir runtime\gold\reviews
   ```

   Each decision is bound to `case_sha256`. Editing the query, split, evidence
   snapshot or rubric after review invalidates the review.

5. Merge completed reviewer files into a controlled `reviews.jsonl`, run
   `financial_agent_e2e_review`, and send every `needs_arbitration` case to a
   third reviewer. An arbitrator cannot reuse either primary reviewer identity.

6. Run every case at least four times in the controlled evaluation environment.
   Export only redacted traces with `runtime_snapshot_sha256`,
   `financial-agent-e2e-trace-redaction/v1`, explicit tool outcomes, cost and
   latency. Then freeze the admitted package:

   ```powershell
   python -m evaluation.financial_agent_e2e_gold_freeze `
     --cases runtime\gold\review-cases.jsonl `
     --reviews runtime\gold\reviews.jsonl `
     --runs runtime\gold\runs.jsonl `
     --dataset-id financial-agent-real-v1 `
     --frozen-at 2026-09-30T00:00:00+10:00 `
     --train-separation "Test cases were hidden from prompt, model and retrieval tuning." `
     --out runtime\gold\gold-freeze.json
   ```

7. Build `evaluation/releases/active/release-evidence.json`. It must list every
   Gold and metric artifact with its exact byte SHA-256. `metric_sources` binds
   every candidate/baseline RAG, E2E and citation metric plus latency, cost and
   red-team values to an artifact JSON Pointer. Verify it locally:

   ```powershell
   python -m evaluation.release_quality_evidence `
     --spec evaluation\releases\active\release-evidence.json `
     --root . `
     --out runtime\reports\release-quality-evidence.json
   ```

8. Only after the evidence verifier returns `release_allowed=true`, set the
   GitHub repository variable `ENFORCE_PRODUCTION_QUALITY_GATE=true`. From that
   point, a missing, edited, incomplete or regressed evidence package blocks
   the backend deployment job.

The repository currently contains no real production Gold or active evidence
bundle. Existing 96-case review queues remain candidate-only and cannot be used
to activate the gate.
