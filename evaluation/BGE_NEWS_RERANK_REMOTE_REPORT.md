# BGE Cross-Encoder news-reranking A/B

Date: 2026-08-13

## Protocol

- Corpus: 1,408 persisted news rows from the remote PostgreSQL snapshot.
- Set: 10 fixed internal public-news questions; not a human-Gold or production
  accuracy claim.
- First stage: stock-scoped news-only BM25; Top-5 output.
- Treatment: `BAAI/bge-reranker-v2-m3` Cross-Encoder reranks BM25's Top-20
  candidates before selecting Top-5.  The same generator, judge,
  `text-embedding-v3`, and RAGAS 0.2.15 configuration were used for both arms.

## Results

| Strategy | Faithfulness | Answer Relevancy | Context Recall | Context Precision |
| --- | ---: | ---: | ---: | ---: |
| Stock-scoped BM25 | **0.9653** | 0.5899 | **0.4500** | **0.6287** |
| BM25 + BGE Cross-Encoder | 0.9375 | **0.6829** | 0.3417 | 0.6056 |
| Delta | -0.0278 | +0.0930 | -0.1083 | -0.0231 |

The independent keyword-coverage diagnostic moved from `0.5167` (BM25) to
`0.4253` (BGE), consistent with the Context Recall regression.

## 候选池深度口径修正与 20 → 10 → 5 实验（2026-08-13）

对离线评测器复核后发现：此前 `_subset_bge_news_reranked` 接受了
`candidate_k` 参数，但实际只把 `top_k` 条送给 BGE。因此上方初版报告里
标作“候选池 20”的 RAGAS 行，**不能用于比较候选池深度**；它们保留为当时的
固定集答案评测记录，但不再作为 Top-20 证据。

已修正评测器并在相同远端只读快照上重新执行真正的候选池扩展：348 条实体校验
新闻、10 个固定公开新闻问题、标题证据模式、生成上下文 Top-5。先报告不调用
Judge 的关键词覆盖诊断；因为所有候选策略均低于基线，所以没有继续消耗 Judge
调用运行 RAGAS。

| 策略 | 实际候选/筛选路径 | 关键词覆盖诊断 |
| --- | --- | ---: |
| 分面 BM25 基线 | Top-5 | **0.4836** |
| BGE 纯重排 | BM25 Top-20 → BGE Top-5 | 0.4351 |
| BGE/BM25 融合 | BM25 Top-20 → 50/50 融合 Top-5 | 0.4711 |
| 分阶段漏斗 | BM25 Top-20 → BGE Top-10 → 分面覆盖/BGE Top-5 | 0.4410 |

结论：在此 10 条固定集上，通用 `20 → 10 → 5` 没有提高召回诊断，也没有超过
当前 Top-5 分面 BM25。它不进入生产默认路径。`candidate_k=20` 仍仅作为明确
离线实验参数，不能引用旧 RAGAS 行宣称候选池效果。

## Decision boundary

The production implementation uses BGE as the default Candidate-5 reranker as
requested, with an offline local-model requirement and BM25 fallback when it
is missing or fails.  The numbers above do **not** establish that this default
improves quality.  Treat BGE as an integrated, observable production
experiment until one of these changes produces a non-regressing RAGAS result:

1. tune the candidate-pool size and preserve high-confidence BM25 evidence;
2. rerank title plus a short query-relevant body snippet rather than title-only
   evidence;
3. introduce human-Gold retrieval labels and report Recall@20 to Recall@5
   loss separately from answer evaluation.

Raw ignored artifacts:

- `runtime/reports/remote-db-bge-news-rerank-k5.json`
- `runtime/reports/ragas-bge-ab-bm25.json`
- `runtime/reports/ragas-bge-ab-reranked.json`

## Current production guardrail: entity-verified, set-preserving BGE

The upstream per-stock news endpoint also returned sector listicles and other
companies' headlines.  Production now accepts a news title only when it names
the requested ticker/name or a trusted alias obtained from that company's
official disclosures.  The live source uses the ticker/local canonical name;
the persisted source also uses official-disclosure aliases (for example,
`603501`'s `豪威集团` alias for `韦尔股份`).  Official announcements remain a
separate fill-only source.

On the same read-only snapshot, this reduced the news corpus from 1,408 raw
rows to 348 entity-verified rows.  The default BGE pass now reorders exactly
the lexical/facet Top-5 selected for the answer model; it cannot replace that
evidence set.  Wider pools require an explicit environment override and remain
an offline experiment.

| Strategy (entity-verified Top-5) | Faithfulness | Answer Relevancy | Context Recall | Context Precision |
| --- | ---: | ---: | ---: | ---: |
| BM25/facet order | **0.9381** | **0.7950** | 0.3833 | 0.5693 |
| Default BGE safe reorder | 0.8445 | 0.7816 | **0.4500** | **0.6656** |

Both rows use the fixed 10-question set, the same evaluation configuration,
and entity-verified title evidence; answer generation is run separately per
retrieval order.  The result is a trade-off rather than a universal quality
gain, so BGE is the default **observable reranker**, not a claim of across-the-
board superiority.  BM25 remains the failure fallback and telemetry identifies
the applied rerank method.

## 规范名实体门禁收紧（2026-08-13）

复核候选标题后发现，部分错误绑定的上游新闻行可凭其自身存储的 `stock_name`
通过门禁。新闻行的名称不是权威实体来源，因此检索与离线评测现在只接受：

1. 本地股票代码字典的规范证券简称；
2. 该股票官方公告中出现的受信任简称；
3. 标题中的证券代码；
4. 官方公告本身（作为独立 primary-source 分支）。

在相同远端只读快照中，实体校验新闻从 **348** 条降为 **306** 条，删除 42 条
依赖上游错标名称的候选。10 条固定集上的 scoped Top-5 关键词覆盖仍为 `0.4836`；
这证明本次变更是来源精度和错误实体暴露面的收紧，而不是固定集指标提升。尚无人工
相关性标注，因此不能把“删除 42 条”量化为 Precision@5 提升。
