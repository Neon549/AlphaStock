# Memory Index Corpus and Evaluation Contract

## What may enter the index

This index contains reusable operating knowledge only. Its seven controlled
scopes are `governance`, `research`, `retrieval`, `workflow`, `operations`,
`backtest`, and `evaluation`. Each approved Markdown file must state a scope,
human owner, version, `evidence_class: operating_knowledge`, and
`market_fact_policy: never_override_current_evidence` in front matter.

Candidates flow as follows:

`production Bad Case / backtest deviation / human post-mortem -> pending -> human review -> approved Markdown -> explicit index sync`.

Approval requires a concrete observed failure or verified procedure, a
repeatable action, a stated scope, no live market fact or return promise, and a
review note. Rejection leaves no retrievable Markdown. Changes to an approved
lesson create a new version and are evaluated before reindexing.

## Scale plan

The target corpus is thousands of approved lessons, not thousands of generated
files. Collect by taxonomy and source: initial curated cases, production Bad
Cases, and backtest/review deviations. Keep source run IDs and reviewer IDs in
the candidate store. Do not use a model-generated candidate as its own proof.

The target 2,000-case evaluation set is stratified by scope and difficulty:

| Split | Count | Purpose |
| --- | ---: | --- |
| development | 1,200 | retrieval tuning; never report as final quality |
| validation | 400 | threshold selection |
| hidden | 400 | release gate; unavailable to prompt/index tuning |

Every case records query, expected source paths, forbidden source paths,
scope, expected evidence class, and whether a real-time claim must be refused.
The hidden split lives outside the repository/CI workspace available to tuning
jobs; CI receives only its signed aggregate result.

## Metrics and gates

Evaluate per scope as well as overall:

- `Recall@K`, `MRR`, and `Precision@K` for intended lesson retrieval;
- forbidden-recall rate for a retrieved but prohibited source;
- evidence-class compliance: every returned chunk is operating knowledge;
- current-evidence override rate: a memory-based answer must not be marked as
  support for a live price, current financial metric, or current news claim.

There is no fixed universal percentage gate before a baseline exists. Each
release must not regress the immutable validation/hidden baseline; all critical
governance cases and evidence-class checks must be 100%.
