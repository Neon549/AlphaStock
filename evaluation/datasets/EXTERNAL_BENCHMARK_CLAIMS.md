# External benchmark reporting and resume wording

## FinanceBench open-source v1

The AlphaStock repository includes a separate `external_gold` evaluation tier
based on the public 150-case FinanceBench sample. Its source annotations
contain a human answer, justification, evidence text and evidence PDF page;
the source SEC-filing PDFs are pinned by SHA-256. This is an external
financial-document benchmark, not AlphaStock online traffic and not a claim
about investment performance or production quality.

Run it with:

```powershell
python -m evaluation.import_financebench
python -m evaluation.run_financebench_eval --out runtime/reports/financebench-v1.retrieval.json
```

Current BM25 baseline, with top 10 returned pages:

| Protocol | Recall@10 | MRR | NDCG@10 | Citation hit rate |
| --- | ---: | ---: | ---: | ---: |
| Full 84-document corpus | 13.67% | 0.1031 | 0.1106 | 14.00% |
| Deterministic company/report-period scoping inferred from the question | 23.67% | 0.1480 | 0.1668 | 25.33% |
| Gold document metadata supplied; page retrieval only | 39.33% | 0.5389 | 0.2634 | 42.00% |

The final row is a document-within-page retrieval diagnostic. It must be
labelled as **Gold-document-scoped**, not as end-to-end RAG or document
discovery. The first row is the appropriate metadata-free full-corpus result.

## Resume-safe wording

Chinese:

> 搭建页码可追溯的财报 RAG 评测链路，并接入 FinanceBench 公开人工标注基准（150 条金融 QA、84 份 SEC 文件）；在全库和文档范围已知两种协议下报告 Recall@10/MRR/NDCG，沉淀可复现实验快照与证据页回链。

English:

> Built a page-citable financial RAG evaluation pipeline and integrated the public human-annotated FinanceBench benchmark (150 QA cases across 84 SEC filings); reported reproducible Recall@10, MRR and NDCG under full-corpus and gold-document-scoped retrieval protocols.

Only include a number when the protocol is in the same bullet or nearby, for
example: “39.33% Recall@10 in the Gold-document-scoped page-retrieval
protocol.” Do not describe this as “39.33% RAG accuracy,” “production
accuracy,” or “real-user accuracy.”
