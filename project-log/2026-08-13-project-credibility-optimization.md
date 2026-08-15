# Project credibility optimization log - 2026-08-13

## Objective

Improve AlphaStock's **verifiable engineering capability** and make its public
claims match the available evidence. This log deliberately excludes TechLens
training/model work and resume-PDF layout work.

## Modification record

### 1. Unified the regression gate and made it offline by construction

**Problem observed**

- Local `pytest` collected 250 tests while the GitHub Actions deployment gate
  used `unittest discover` and ran only 197 tests.
- `config/llm_config.py` loaded `.env` with `override=True`, so a developer
  credential could override CI placeholders. The supposed offline gate could
  therefore issue a real provider request.

**Change**

- `.github/workflows/deploy.yml`: run `python -m pytest -q tests`, set
  `ALPHASTOCK_SKIP_DOTENV=1` and `ALPHASTOCK_OFFLINE_TESTS=1`.
- `config/llm_config.py`: environment variables take precedence over `.env`;
  the new explicit flag skips dotenv loading for CI/offline regression.
- `tests/conftest.py`: deny non-loopback TCP connections for offline pytest.
  Loopback remains available for Starlette TestClient and asyncio internals.
- `tests/unit/test_llm_config_environment.py`: regression-proof that the CI
  placeholder remains active even when a local `.env` exists.

**Verification**

```powershell
$env:DEEPSEEK_API_KEY='ci-test-placeholder'
$env:DASHSCOPE_API_KEY='ci-test-placeholder'
$env:ALPHASTOCK_SKIP_DOTENV='1'
$env:ALPHASTOCK_OFFLINE_TESTS='1'
python -B -m pytest -q tests
```

Result: **251 passed, 5 warnings, 19.65s**. The warnings are dependency/API
deprecations (jieba/pkg_resources, Starlette TestClient and a Pydantic forward
reference); they do not indicate a failed test.

**Claim now supported**

> CI and local regression share a 251-test deterministic pytest suite. The
> offline gate does not load local dotenv credentials and blocks non-loopback
> network access.

Do not claim that this is a load test, a security penetration test, or proof
that every external integration works online.

### 2. Corrected the product capability boundary

**Problem observed**

The README mixed research assistance, trading decision wording and screening
claims. This could imply broker execution or investment guarantees even though
the task graph deliberately leaves `trade_action` unbound.

**Change**

- `README.md`: define the project as an evidence-governed research assistant
  that produces reviewable drafts and does not submit broker orders.
- Added an operational capability table distinguishing: governed research,
  document/news RAG, research backtests, compound-task routing and unbound
  trading.
- Updated the technical stack description for approved-memory retrieval. The
  TechLens stack/model claim is intentionally out of scope for this change.

**Claim now supported**

> The system has a governed research workflow with human-review boundaries;
> it is not an automated brokerage or return-guarantee system.

### 3. Reframed RAG results by evidence tier

**Problem observed**

Historical internal ablation/RAGAS numbers were easy to read as end-to-end or
production quality. The new BGE experiment has metric trade-offs rather than a
universal quality improvement.

**Change**

- `README.md`: label historical BM25/dense/RRF ablation explicitly as an
  internal retrieval diagnostic.
- Add a result-boundary table that links to the external FinanceBench result,
  metadata-free retrieval result and fixed-news BGE A/B result.
- Make BGE's set-preserving, entity-verified policy and BM25 fallback visible.

**Current data that can be stated with protocol**

| Tier | Protocol | Result |
|---|---|---:|
| FinanceBench external Gold | 150 public QA / 84 SEC filings | 43.24% judgeable answer accuracy; 30.41% citation-grounded answer accuracy |
| FinanceBench retrieval | Metadata-free full 84-document corpus, Top-10 pages | 13.67% Recall@10 |
| Internal news BGE A/B | 10 fixed public-news queries, entity-verified Top-5 | BGE: recall/precision trade-off; **not** universal lift |

The definitions and artifacts remain in:

- `evaluation/E2E_EVAL_REPORT.md`
- `evaluation/BGE_NEWS_RERANK_REMOTE_REPORT.md`
- `evaluation/datasets/EXTERNAL_BENCHMARK_CLAIMS.md`

### 4. Added controlled dynamic subagent instantiation

**Problem observed**

The parent could choose among four fixed subagent roles but could not form a
request-specific reviewer. A generic Fork Agent or arbitrary agent-to-agent
chat would allow unreviewed prompts, tools and permissions to spread beyond
the Harness.

**Change**

- `agent_runtime/agents/subagents.py`: add the one-shot
  `EphemeralSubagentFactory` with code-reviewed `evidence-critic` and
  `risk-reviewer` templates.
- `agent_runtime/agents/investment_harness.py`: the parent may emit a bounded
  `create_subagent` action after evidence exists. The instance receives at
  most eight compact observations, zero tools/permissions, runs once, then
  records create/result/destroy lifecycle events.
- Target and observations are JSON-encoded as untrusted task data, so they
  cannot override the fixed review policy through prompt injection.
- Added unit and integration tests plus the OpenSpec and subagent contract
  documentation.

**Verification**

```powershell
python -B -m pytest -q tests/unit/test_subagent_registry.py tests/integration/test_investment_agent_loop.py
```

Result: **13 passed**.

Final offline regression after this change: **254 passed, 5 warnings, 20.17s**.

**Claim now supported**

> The parent dynamically instantiates at most one request-scoped review child
> from approved templates, traces its lifecycle and destroys it after one
> result. It is not a process-level Fork Agent, persistent worker or dynamic
> code/tool generator.

### 5. 验证并否决新闻 RAG 的通用 20 → 10 → 5 漏斗

**问题与发现**

需要验证“BM25 Top-20 → BGE Top-10 → Top-5”是否能同时提高召回和精度。复核时
还发现离线新闻评测器虽然接收 `candidate_k`，但没有实际把候选池扩展到该大小；
旧的“Top-20”结果不能用于候选深度比较。

**改动与实验**

- 修复 `evaluation/run_remote_db_retrieval_eval.py`，让新闻 BGE 重排真实使用
  `max(top_k, candidate_k)` 条分面 BM25 候选。
- 增加 `bm25_scoped_bge_20_10_5_faceted`：实体校验语料 → 分面 BM25 Top-20 →
  BGE Top-10 → 分面覆盖 + BGE 排序 Top-5。
- 添加候选池大小和分面覆盖回归测试；新增 OpenSpec 使用中文。

**相同只读快照上的诊断结果**

| 策略 | 关键词覆盖诊断 |
|---|---:|
| 分面 BM25 Top-5 | **0.4836** |
| BGE Top-20 → Top-5 | 0.4351 |
| BGE/BM25 50/50，Top-20 → Top-5 | 0.4711 |
| Top-20 → Top-10 → Top-5 | 0.4410 |

**决定**

三级漏斗没有通过第一道检索诊断，未运行 RAGAS，也未改变生产默认的集合内
Top-5 重排。该结果证明的是“当前固定集不支持该通用策略”，不是对所有查询的
泛化结论。

### 6. 收紧错标新闻的实体来源门禁

**问题**

远端新闻行的 `stock_name` 来自上游 feed。若它被错误绑定，旧门禁会把该行自己的
名称当作“该股票实体名”，进而让其他公司或行业标题进入当前股票候选集。

**改动**

- `rag/news_indexer.py`：新闻标题只匹配本地股票代码字典规范名、官方公告别名或
  证券代码；不再信任新闻行自身 `stock_name`。
- `evaluation/run_remote_db_retrieval_eval.py` 镜像同一规则，避免离线与线上口径
  漂移。

**结果**

同一只读快照的实体校验新闻由 **348** 降至 **306**，剔除 42 条错标候选；10 条
固定集的 scoped Top-5 关键词覆盖仍为 `0.4836`。因此这是 precision/safety 方向的
数据清洗，不宣称 Precision@K 提升（当前没有人工相关性标注）。

## Remaining work deliberately not claimed as complete

1. Add human-reviewed Chinese production-like RAG Gold data (target defined in
   `evaluation/datasets/PRODUCTION_EVAL_PROTOCOL.md`).
2. Add load/SLO data: concurrency, P95/P99 latency, provider failure rate and
   recovery success rate.
3. Pin dependencies and use an immutable deployment revision plus health check
   and rollback, instead of server-side `git pull`.
4. Resolve test warnings: Starlette/httpx deprecation and Pydantic forward
   reference.

### 7. 意图路由：从固定 smoke 升级为分桶候选评测，并收紧多股票与缺失时间窗

**问题**

- 历史 fastText 验证只有 12 条，`precision@1 / recall@1 = 0.9167` 只能作为
  小样本基线；错一条即改变 8.33 个百分点。
- 16 条端到端 smoke 全量通过，但主要是规则回归，不能用于线上准确率结论。
- 旧单股票任务契约会在多个股票名同时出现时选择一个局部匹配；回测也没有把
  时间窗作为可阻塞槽位显式暴露。

**改动**

- `api/intent_parser.py`：唯一、非行业泛词简称与少量高频错别字走确定性规则；
  多个已验证股票同时出现在操作性请求时返回澄清警告，绝不静默选择一只。
- `backtest_window` 成为回测任务槽位；缺失时保留任务图并写入
  `missing_slots`，由执行层阻塞并发起澄清。显式代码买卖仍保留
  `trade_action + requires_confirmation`，无代码交易请求同样有缺失槽位。
- `scripts/evaluate_intent_routing.py`：增加按桶端到端结果、澄清 Precision /
  Recall / F1、高风险路由 Precision / Recall / F1 和缺失槽位 exact match。
- 新增 21 条候选级压力集、清单哈希与中文 OpenSpec。样例覆盖简称、错别字、
  多股票、分析面、回测时间窗、缺失澄清、高风险交易、复合任务和讨论硬负例。

**候选集结果与边界**

在 `intent-routing-robustness-candidate-v1` 上，意图 macro-F1、槽位、任务图、
澄清、高风险路由及各桶端到端均为 1.0，21 条都由规则层处理，LLM fallback 为
0。这只是项目作者构造的 candidate fixture，用于证明上述契约没有回归；它
不是脱敏线上日志、不是独立人工复核集，不能作为简历或线上准确率指标。

**仍需完成**

采集经合规脱敏、独立标注且与训练集隔离的真实表达，至少按错别字、简称、
多股票、上下文指代、复合任务、交易、时间/范围缺失分桶，并在冻结后才报告
macro-F1、混淆矩阵、置信度校准和线上误路由率。

### 8. 受约束 Query Rewrite 与复杂研究请求分解

**问题**

已有 `expand_finance_query()` 只做 BM25 同义词追加，未形成可审计的实体/时间
改写契约；而普通复合请求的确定性 DAG 无法表达多股票比较或某些条件、多跳研究
需求。直接开放 LLM Rewrite 或自由 Planner 会让模型猜代码、篡改年份/金额，或
发明权限和交易动作。

**改动**

- 新增 `rag/query_rewrite.py`：输出 `original_query`、`rewritten_query`、
  `rewrite_reason`、`filters` 和来源。实体只来自本地字典或显式代码；“最近”成为
  30 天新闻过滤，“2025 年年报”成为 `report_period` 元数据；回购、利润、营收和
  调价同义词仅追加而不覆盖原文字面。
- 新闻 BM25、BGE 与 dense fill 使用改写检索词；审计仍保留原问题。RAG telemetry
  仅记录改写前后的脱敏 hash/preview、原因和过滤字段。
- 新增 `agent_runtime/planning/constrained_decomposition.py`。只有复杂只读请求才
  调 LLM，并接收受限 JSON：仅 `investment_analysis` / `comparison`、最多三任务、
  focus 白名单、依赖只能指向更早任务、代码只能复用原问中本地验证过的代码。
- 无效 JSON、模型故障、虚构代码和循环/越界依赖一律回退到原确定性结果。交易词
  根本不进入该路径；`comparison` 没有本地执行绑定，明确路由到专用 endpoint。

**验证边界**

单元测试覆盖原始 query 保留、实体/时间/同义词改写、未知代码不规范化、有效比较
任务、虚构代码拒绝、依赖拒绝及 Parser 回退。当前只证明协议和失败闭环，没有人工
标注的复杂真实查询集，不能宣称 Query Rewrite 或 LLM 分解已经提升线上 Recall、
Precision、RAGAS 或意图准确率。

### 9. Query Rewrite A/B：先在冻结候选集证伪宽松扩展，再保留非负门槛

**可用数据审计**

仓库内不存在脱敏真实会话或带独立 reviewer 的复杂 Query Gold。`user_query_intake`
是 manual expert scenario，88 条 filing query variants 是模板生成 candidate；两者都
不能冒充“独立人工复核真实 Query”。因此没有伪造 production 结论。

**候选 A/B**

新增 `evaluation/run_query_rewrite_ab_eval.py`，在相同冻结语料、相同 entity/period
scope、相同既有 filing alias baseline 下，对 88 条 Query Variant 比较是否再加入
新版确定性 Rewrite。首轮发现宽松规则 1 胜 3 负，Recall@10 `-0.0125`；根因是对
已精确的“营业收入”“归属于上市公司股东的净利润”重复扩词。

收紧为只扩展口语/非标准表达后：Recall@10、Precision@10、F1@10、Citation hit 无
变化，MRR `+0.0006`、nDCG@10 `+0.0005`；逐题 1 胜、0 负、87 平。保留该实现，
但只能称为 **candidate non-regression diagnostic**，不能称为生产指标提升。

**人工复核准入条件**

真实 A/B 需要另行采集 `deidentified_session` 或 `production_bad_case`，冻结语料，
两名匿名 reviewer 独立标注 A/B 证据相关性、完整性、页码引用与偏好，并由第三人
仲裁分歧。只有与训练/candidate 隔离的冻结 test 集达到非退化检索、引用、人工偏好
和延迟/成本门槛，才允许扩大 Rewrite 或 LLM decomposition 覆盖。
