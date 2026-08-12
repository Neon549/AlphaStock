# Production Evaluation Admission Protocol

This repository's committed fixtures are regression contracts, not evidence of
production quality.  A dataset can enter the `production` tier only after all
of the following are complete.

1. Freeze the source corpus first. Record its SHA-256/version, document IDs,
   document versions and collection date. Never mix live news retrieval with a
   historical score.
2. Build cases independently from training data. A reviewer labels the answer
   facts, evidence IDs, page-level citations, abstention condition and failure
   type. Do not use an LLM-generated label without human review.
3. Keep source provenance per case: `origin`, reviewer and review date. Remove
   user identifiers before committing a real-query case.
4. Validate the JSONL before adding it to the manifest:

   ```bash
   python -m evaluation.frozen_dataset --kind rag --tier production --dataset path/to/rag.jsonl
   python -m evaluation.frozen_dataset --kind intent --tier production --dataset path/to/intent.jsonl
   ```

5. Add the immutable dataset to `DATASET_MANIFEST.json` as `tier: production`.
   It must include `review_protocol`, `frozen_at`, `corpus_snapshot`, and a
   statement describing train/evaluation separation; then recalculate the file
   SHA-256 and case count.
6. Report the dataset version, count, split, metric definition, baseline and
   confidence interval alongside every resume- or interview-facing number.

For a final RAG `test` set, run the stricter untouched-real-query audit as
well. It rejects non-deidentified origins, common identity fields/patterns and
overlap with the retriever-selection datasets by normalised query, cited source
document or labelled fact:

```bash
python -m evaluation.real_rag_test_admission ^
  --dataset path/to/rag-real-final-test.jsonl ^
  --out runtime/reports/rag-real-final-test.admission.json
```

Recommended first scope: 200--300 RAG cases across annual reports, announcements,
tables, cross-section questions and abstentions; plus 150--300 independently
reviewed routing cases with typos, aliases, missing slots, multi-intent queries,
prompt-injection attempts and high-risk side effects. These are targets, not
numbers that may be claimed before collection.

## Public filing seed corpus

`evaluation/corpus/production_candidate_v1/sources.json` defines the first
10-document public-filing candidate batch. It spans consumer, new energy,
banking, auto, semiconductor equipment, photovoltaic, smart IoT, agriculture
and manufacturing. Download and hash the exact PDF bytes before parsing:

```bash
python -m evaluation.download_corpus --snapshot-out runtime/reports/public-filings-candidate-v1.snapshot.json
```

The downloaded PDFs stay outside Git. The generated snapshot is the only
acceptable corpus version for labeling and retrieval comparison; do not mix a
later re-download into the same Golden Set.

After locking a snapshot, run the PDF preflight before generating chunks. It
verifies every local PDF hash and lists pages that PyMuPDF cannot read well:

```bash
python -m evaluation.corpus_preflight --out runtime/reports/public-filings-candidate-v1.preflight.json
```

Low-text pages are parser-review candidates for MinerU/OCR; they must not be
silently indexed as empty evidence.

Build the page-citable PyMuPDF baseline corpus after preflight:

```bash
python -m evaluation.build_pdf_corpus ^
  --chunks-out runtime/reports/public-filings-candidate-v1.chunks.jsonl ^
  --metadata-out runtime/reports/public-filings-candidate-v1.corpus.json
```

Each candidate chunk retains `document_id`, report period, source hash, page
and detected section path. Low-text pages remain outside this baseline until a
separate MinerU/OCR pass has been reviewed.

`rag_candidates.jsonl` currently contains 20 page-citable fact questions and
2 report-period abstention cases derived from that snapshot. It is explicitly
`candidate_pending_human_review`; run the baseline to expose retrieval issues,
then have a second reviewer validate every fact and citation before promotion:

```bash
python -m evaluation.run_candidate_rag_eval ^
  --out runtime/reports/public-filings-candidate-v1.bm25.json
```
