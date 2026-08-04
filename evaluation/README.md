# Evaluation guide

`evaluator.py` and its JSON reports are historical exploratory artifacts, not
release gates: its legacy vector-only function shares a hybrid entrypoint and
the news source is live. Do not use those stored scores as a reproducible
pgvector-versus-hybrid comparison.

The current offline release gate is:

```bash
python evaluation/regression_runner.py
```

It checks deterministic workflow controls and runs in GitHub Actions before a
main-branch deployment. The 8 committed cases are intentionally a smoke set:
all must pass.

`rag_golden_eval.py` now provides deterministic snapshot scoring for
Recall@K, MRR, nDCG, citation hit rate, citation backlink correctness,
abstention compliance and unsupported-answer rate. Its committed
`fixtures/rag_corpus_v1.jsonl` plus `datasets/rag_golden_seed.jsonl` are a
small contract fixture, not a claim of production quality.

The next real RAG gate is to ingest a fixed, reviewed document corpus snapshot
and expand the seed to a human-reviewed Golden Set. Then run independent
`pgvector_only`, `bm25_only`, and `hybrid_rrf` adapters against exactly that
same corpus; do not score against live news or a changing uploaded-document
table.
