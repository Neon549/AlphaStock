# End-to-end RAG benchmark matrix

| Dataset | Cases | Evidence status | Can report answer accuracy? | Claim boundary |
|---|---:|---|---|---|
| FinanceBench open-source v1 | 150 | Public human answer/evidence/page annotations | Yes, with automated judge explicitly named | External benchmark; not production traffic |
| `rag_golden_seed` | 3 | Human-reviewed fixtures | Yes, regression accuracy only | Small deterministic regression set |
| `production_candidate_v1` | 22 | Candidate labels; independent review pending | Yes, as internal candidate evaluation | Do not call production Gold |
| `heldout_public_filings_v1/v2` | 25 / 22 | Candidate expert mappings; freeze/review pending | Yes, as validation diagnostics | Do not use for final resume claim yet |
| CFQA candidate set | 20 | Public QA answers, PDF/evidence mapping pending | Answer-only diagnostics; not grounded RAG yet | Do not claim citation-grounded accuracy |

Completed run summary is recorded in
[`evaluation/E2E_EVAL_REPORT.md`](../E2E_EVAL_REPORT.md). It reports the
public FinanceBench result separately from the internal candidate/validation
sets and includes the conservative all-case lower bound for API timeouts.

The end-to-end runner reports `answer_accuracy` and
`grounded_answer_accuracy` separately. `grounded_answer_accuracy` requires a
correct benchmark answer, all required citations, and every cited page to be
among retrieved evidence. RAGAS Faithfulness remains a separate support metric
and is never substituted for answer correctness.
