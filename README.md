# AlphaStock · A-Share Intelligent Research Assistant

> Evidence-governed A-share research assistant combining fundamental, technical, sentiment and backtest workflows. It produces reviewable research drafts; it does not submit broker orders or provide automated investment execution.

🌐 **Live Demo**: [alphastock.cloud](https://alphastock.cloud) · 📦 **Source**: [Neon549/Alpha_stock](https://github.com/Neon549/Alpha_stock)

---

## What It Does

| Feature | Description |
|---|---|
| **Stock Analysis** | Input a ticker — fundamental / technical / sentiment research paths produce an evidence-linked draft, risk notes and a human-review boundary |
| **Quantitative Backtest** | KDJ+MACD / RSI / Bollinger Band strategies with grid-search parameter optimization (36 combinations); outputs Sharpe ratio, max drawdown, win rate |
| **News Sentiment** | Stock-scoped A-share news retrieval via BM25 + pgvector + RRF, with entity verification and a BGE Cross-Encoder rerank safety fallback |
| **Buy Signal Screener** | Market-universe scan for configured technical signals and factor scores; results are research candidates, not orders or return guarantees |
| **Multimodal Input** | Chart image analysis via Qwen-VL-Plus |

---

## Architecture

```
User Request
   │
   ├── Intent Recognition  (4-class: discussion / analysis / system / insufficient)
   │     └── Slot Extraction  →  stock_code, analyst_focus, reply_hint
   │
   ├── Stock Analysis  (Python state machine; LangGraph compatibility adapter)
   │     ├── FundamentalAnalyst    PE / PB / ROE / revenue growth
   │     ├── TechnicalAnalyst      K-line / trend / volume  ← TechLens-1.5B local model
   │     └── SentimentAnalyst      RAG news retrieval + sentiment scoring
   │           validation_node     hallucination firewall  [ANALYSIS_OK / ABORT]
   │           researcher_node     bull/bear debate
   │           trader_node         final decision + long-term memory write
   │
   └── Quantitative Backtest  (Python fixed runtime; LangGraph compatibility adapter)
         backtest_node         AKShare data + backtrader engine
         interpreter_node      RAG strategy knowledge + LLM interpretation
         optimizer_node        grid search over 36 parameter combinations
```

---

### Source layout (current)

```text
control_plane/  event routing, run lifecycle and per-run model profile
agent_runtime/  agents, context, memory, skills, workflows and compatibility adapters
agent_runtime/compat/langgraph/  opt-in LangGraph adapter for comparison and rollback
api/            FastAPI delivery endpoints
frontend/       React + Vite product interface and legacy static page
backtest/       quantitative domain services
rag/, tools/    retrieval and external data integrations
evaluation/     offline evaluation runners and datasets
tests/          unit, workflow, integration and evaluation tests
runtime/        ignored local state: reports, caches and checkpoints
```

`InvestmentRuntime` defaults to a governed Agent Loop: the model may choose
among analysis, document RAG, backtest and approved-memory search, while the
Harness enforces a static read-only allowlist, duplicate suppression, a four-step
budget, evidence persistence and deterministic publication governance.
`PythonInvestmentRuntime` is retained as the fixed-workflow fallback
(`INVESTMENT_EXECUTION_MODE=workflow`); `LangGraphInvestmentRuntime` remains an
opt-in compatibility adapter for rollback and cross-runtime comparison.

## Tech Stack

| Layer | Technology |
|---|---|
| Agent Orchestration | Governed Agent Loop (default) + Python fixed-workflow / LangGraph compatibility fallbacks |
| LLM | DeepSeek V3 (primary) / Qwen (auto-fallback) |
| Technical Analysis Model | TechLens-1.5B (Qwen3 fine-tuned, local inference) |
| RAG Retrieval | BM25 + pgvector + RRF hybrid search |
| Vector Store | PostgreSQL 14 + pgvector (50k-row LRU eviction) |
| Embedding | shibing624/text2vec-base-chinese |
| Backtest Engine | backtrader + quantstats |
| Market Data | AKShare (real-time) |
| Long-term Memory | PostgreSQL + pgvector (conversation persistence and human-approved memory retrieval) |
| Observability | LangFuse v2 (full LLM trace via Docker) |
| Auth | Google OAuth / GitHub OAuth / Email verification (Aliyun Direct Mail) |
| Backend API | FastAPI |
| Frontend | React 18 + Vite (`frontend/react-app`) |
| Deployment | Tencent Cloud · Nginx · Let's Encrypt SSL · UptimeRobot |

---

## Key Design Decisions

### Hallucination Control
- Ticker names resolved against a local dictionary of 1,500+ A-share stocks — never inferred by the LLM
- Every analyst output enforces `[ANALYSIS_OK]` / `[ANALYSIS_ABORT]` tagging; malformed or unsupported outputs are blocked before reaching downstream nodes
- Tool layer validates all returned data types before they enter the LangGraph state

### Hybrid Retrieval (RAG)
BM25 handles exact-match terms (ticker codes, indicator names); pgvector handles semantic similarity; RRF merges both ranked lists without manual weight tuning.

Current market-price and financial-indicator tool results are also captured as
structured, append-only evidence snapshots in PostgreSQL table
`market_evidence`. The table keeps stock code, quote/report time, retrieval
time, source, quality status, typed JSONB metrics, content hash and the
auditable Agent `result_ref`; the human-readable tool payload remains in the
existing tool-result artifact store. Missing or stale timestamps are retained
with an explicit quality status and are not silently treated as current data.
Bounded daily K-line history is stored as `daily_history` evidence when the
research Agent selects `market-history`. Recent snapshots are available from
`GET /api/v1/stocks/evidence/{stock_code}` with optional `evidence_type` and
`limit` filters.

The following ablation is a historical internal retrieval diagnostic, not an
external answer-accuracy or production-quality claim. Current public and
end-to-end results are reported separately in [RAG Evaluation](#rag-evaluation).

Ablation results across four retrieval strategies (Recall@10):

| Strategy | Recall@10 |
|---|---|
| BM25 only | 0.8448 |
| Dense vector only | 0.8362 |
| Simple weighted mix | ~0.79 |
| **Hybrid + RRF** | **0.8707** ✅ |

### Structured Query Understanding

Intent routing uses three layers: deterministic rules first, then a high-confidence local fastText four-class classifier (`discussion` / `analysis` / `system` / `insufficient`), and finally the LLM JSON parser for low-confidence or unfamiliar inputs. For an analysis intent, fastText only returns directly when the stock name can be resolved by the local mapping; otherwise the request falls back to the LLM so stock and analyst-focus slots are not lost. The `analysts_node` reads `analyst_focus` and skips irrelevant analysts (returning `[SKIPPED]`).

Compound requests are not a fifth classifier label: the deterministic orchestration layer emits a backward-compatible `compound_intent` contract and a task DAG.  It distinguishes sequential actions, independent parallel actions, and confirmation-gated trade requests; a technical + fundamental request remains one analysis task.  The scope, safety boundaries and frozen smoke-evaluation contract are in [`openspec/compound-intent-routing/spec.md`](openspec/compound-intent-routing/spec.md).

The routing contract also fails closed for operational requests that mention
multiple verified stocks, instead of silently selecting one ticker. Backtests
carry an explicit `backtest_window`; a missing window becomes a blocked slot
for clarification. The bucketed robustness fixture covers aliases, typos,
multi-stock ambiguity, missing slots, high-risk wording and compound routes.
Its results are candidate diagnostics—not online intent accuracy—because the
cases are authored stress tests pending independent review. See the Chinese
OpenSpec at [`openspec/intent-routing-robustness/spec.md`](openspec/intent-routing-robustness/spec.md).

Retrieval uses an auditable deterministic query-rewrite plan: locally verified
entity canonicalisation, explicit time filters and finance synonym expansion.
The original query remains the audit record; the rewritten form is retrieval
input only and can never become a fact. Complex read-only comparison or
multi-hop requests may use constrained LLM decomposition, whose JSON can only
reuse verified in-query tickers and allowlisted research task types; invalid
plans fall back to deterministic routing. See the Chinese contract at
[`openspec/constrained-query-rewrite-and-decomposition/spec.md`](openspec/constrained-query-rewrite-and-decomposition/spec.md).

For evidence conflicts or downside-risk review, the parent may create one
request-scoped ephemeral reviewer from the `evidence-critic` or `risk-reviewer`
template. It receives only compact prior observations, has no tools or write
permissions, and emits created/result/destroyed lifecycle trace events in the
same request. This is controlled dynamic instantiation, not arbitrary runtime
code generation or peer-to-peer agent chat. See
[`agent_runtime/agents/SUBAGENTS.md`](agent_runtime/agents/SUBAGENTS.md).

### Skill Registry

Each skill is registered by `agent_runtime/skills/<skill>/skill.json` with a name, description, trigger, required permissions, semantic version and prompt files. The registry creates a content hash from the manifest and prompt references, so any rule change produces a new traceable `version_id`. `/chat` and `/analyze` permission-filter candidates before asking the LLM to choose from their descriptions; invalid LLM output falls back to deterministic triggers. `document-rag` is a read-only skill (`document:read`) with an isolated handler that retrieves pgvector evidence and page citations. Inspect active metadata through `GET /api/v1/skills`.

### Remote MCP

The application also exposes a guarded Streamable HTTP MCP endpoint at
`/api/v1/mcp/`. It maps approved external tool calls into the same Gateway and
Python Runtime used by the web API; it does not give an MCP client direct
database, filesystem, publishing or trading access. The initial tool set is
limited to stock-research drafts, bounded historical backtests, strategy
methodology lookup and human-owned document retrieval. See
[`MCP_REMOTE.md`](MCP_REMOTE.md) for scopes, deployment variables and an
end-to-end smoke client. The current server accepts Bearer-capable MCP clients;
first-class Claude remote connector support remains a separate OAuth task.

### Graceful Degradation
- DeepSeek failure → auto-switch to Qwen
- TechLens offline → auto-switch to DeepSeek for technical analysis
- Any analyst `ABORT` → node skipped, pipeline continues unblocked

### Operational boundary and current capability

| Capability | Current behavior | Boundary |
|---|---|---|
| Chat and stock research | Governed Agent Loop chooses only approved read-only skills, retains evidence and returns a reviewable draft | Publication requires authenticated human review when the governance gate says so |
| Publication review | Output gate checks evidence, risk and citations; an independent reviewer then approves or rejects; the requester must provide the final confirmation | Set `PUBLICATION_REVIEWER_USERS` to a comma-separated allowlist; the same account cannot perform both steps |
| Document and news RAG | Returns page/evidence identifiers where source material supports them; news uses stock/entity checks before reranking | Retrieval quality is evaluated separately from answer correctness; fixed-set RAGAS is not a production-quality claim |
| Backtest | Runs configured historical strategies and reports strategy metrics | It is a research simulation; users must set the time split, fees, slippage and benchmark before interpreting results |
| Compound requests | Builds a validated task DAG with parallel, sequential and confirmation-gated boundaries | Tasks without a local skill binding are explicitly routed to their dedicated endpoint rather than silently fabricated |
| Dynamic review child | Creates at most one evidence-critic or risk-reviewer instance after evidence exists | Reads only compact approved observations; zero tools/permissions; destroyed after one result |
| Long-term memory approval | `safe` keeps every candidate pending; `assist` auto-approves low-risk operating lessons and batches the rest; `full_access` requires expiring explicit confirmation and can auto-handle low/medium risk only | Hard-blocked content and high-risk candidates cannot bypass review; approved Markdown still requires explicit `scripts/sync_memory_index.py` |
| Trading | No broker tool is bound to the Agent Loop | A trade request remains confirmation-gated and cannot become an order in this repository |

---

## RAG Evaluation

Full report: [`evaluation/EVAL_REPORT.md`](evaluation/EVAL_REPORT.md)

Latest remote news RAGAS comparison: [`evaluation/RAGAS_REMOTE_REPORT.md`](evaluation/RAGAS_REMOTE_REPORT.md).

The production news path is stock-scoped, news-first BM25 candidate recall
with entity verification (ticker/current name or official-disclosure alias),
followed by a locally cached **BGE Cross-Encoder**
(`BAAI/bge-reranker-v2-m3`) that safely reorders the same Top-5 evidence set.
Multi-facet requests retain facet coverage and an unavailable reranker safely
falls back to BM25.
The implementation and its current validation boundary are recorded in
[`project-log/rag-rerank-mainline.md`](project-log/rag-rerank-mainline.md).
Its completed fixed-set RAGAS A/B is in
[`evaluation/BGE_NEWS_RERANK_REMOTE_REPORT.md`](evaluation/BGE_NEWS_RERANK_REMOTE_REPORT.md):
BGE has mixed fixed-set results, so it must not be presented as a universal
quality improvement. A corrected true Top-20 / 20→10→5 diagnostic did not
beat the current Top-5 faceted-BM25 baseline, so wider pools remain offline
experiments; the current set-preserving policy and numbers are documented in
that report.

### Claim boundary and current external result

| Evaluation tier | Scope | Current result | Correct interpretation |
|---|---|---:|---|
| FinanceBench external Gold | 150 public financial QA across 84 SEC filings | 43.24% judgeable answer accuracy; 30.41% citation-grounded answer accuracy | Public end-to-end benchmark; useful for locating gaps, not a production KPI |
| FinanceBench retrieval | Metadata-free full 84-document corpus, Top-10 pages | 13.67% Recall@10 | Document discovery remains a primary bottleneck |
| Internal fixed news snapshot | 10 public-news queries | BGE improves context recall/precision but regresses faithfulness/relevancy in the entity-verified Top-5 A/B | Observable reranker experiment, not a universal lift claim |

The data protocol, reproducible commands and resume-safe wording are in
[`evaluation/datasets/EXTERNAL_BENCHMARK_CLAIMS.md`](evaluation/datasets/EXTERNAL_BENCHMARK_CLAIMS.md).
The full answer, citation and retrieval metrics are in
[`evaluation/E2E_EVAL_REPORT.md`](evaluation/E2E_EVAL_REPORT.md).

RAGAS is run in a separate evaluation environment (`requirements-ragas.txt`) so it cannot change the production LangChain runtime.  The Python 3.13-compatible runner uses **RAGAS 0.2** with the configured judge model and DashScope `text-embedding-v3`; Answer Relevancy runs at `strictness=1` because the configured compatible endpoint does not support `n > 1`.

| Metric | Dense-only | Hybrid (BM25 + pgvector + RRF) |
|---|---|---|
| **Faithfulness** | 0.854 | **0.952 ✅ (+10%)** |
| Context Recall | 0.567 | 0.567 |
| Context Precision | 0.527 | 0.487 |

Development follows **EDD (Evaluation-Driven Development)**: every retrieval or prompt change runs `evaluation/evaluator.py` before merging.

The online stock corpus combines recent Eastmoney news with filtered primary-source CNInfo disclosures.  News headlines remain the precision-first lexical source; official PDF chunks fill exact earnings, dividend, buyback and personnel facts.  Multi-intent questions reserve one lexical slot per detected finance facet, and duplicate chunks from the same disclosure are collapsed by source URL.  The complete before/after history and claim boundaries are recorded in [`project-log/rag-recall-improvements.md`](project-log/rag-recall-improvements.md).

```bash
# Through a local SSH tunnel to PostgreSQL (defaults to the 10 eval stocks)
python -m scripts.refresh_announcement_index --port 15432 --lookback-days 30

# Fast, model-free retrieval A/B before spending judge-model tokens
python -m evaluation.run_remote_db_retrieval_eval --port 15432 \
  --bm25-only --source-kinds news,announcement --evidence-mode online
```

Online monitoring uses one Langfuse trace per request. It records a redacted query fingerprint, corpus snapshot, top-k distances, rerank state, citation structural validation, abstention, token usage and latency. Public API responses expose only `run_id`, answer/citations and a safe `trace_summary`; prompts and detailed evidence remain in the private audit/Langfuse view. Alerting threshold: Faithfulness < 0.85 triggers review.

---

## Quick Start

```bash
git clone https://github.com/Neon549/Alpha_stock
cd Alpha_stock
pip install -r requirements.txt

# Full deterministic regression suite (the CI command)
# PowerShell
$env:ALPHASTOCK_SKIP_DOTENV='1'; $env:ALPHASTOCK_OFFLINE_TESTS='1'; python -m pytest -q tests
# bash / Linux
ALPHASTOCK_SKIP_DOTENV=1 ALPHASTOCK_OFFLINE_TESTS=1 python -m pytest -q tests

# 训练本地四分类意图模型；生成 models/intent_classifier.bin
python scripts/train_intent_classifier.py
```

Configure `.env`:
```env
DEEPSEEK_API_KEY=your_key
DASHSCOPE_API_KEY=your_key
TUSHARE_TOKEN=your_token
POSTGRES_DSN=postgresql://user:password@localhost:5432/alphastock
LANGFUSE_PUBLIC_KEY=your_key
LANGFUSE_SECRET_KEY=your_key
LANGFUSE_BASE_URL=https://cloud.langfuse.com  # optional; self-hosted deployments may use LANGFUSE_HOST
```

```bash
python -c "from db import init_db; init_db()"
python main.py
```

---

## API Reference

```bash
# Stock analysis
curl -X POST http://localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{"stock_code": "600519"}'

# Quantitative backtest
curl -X POST http://localhost:8000/api/v1/backtest \
  -H "Content-Type: application/json" \
  -d '{"stock_code": "600487", "strategy": "rsi", "start_date": "20220101", "end_date": "20241231"}'

# Health check
curl http://localhost:8000/api/v1/health
```

---

## Backtest Strategies

| Strategy | Entry Signal | Exit Signal | Best For |
|---|---|---|---|
| `kdj_macd` | KDJ golden cross + MACD histogram turns positive | KDJ death cross or MACD turns negative | Trending markets |
| `rsi` | RSI < 30 | RSI > 70 | Mean-reversion / range-bound |
| `boll` | Price crosses above lower band | Price crosses below upper band | Range-bound markets |

Backtest validation on 62 out-of-sample A-share stocks: max drawdown reduced from **24.6% → 15.2%** after adding position filters (-2% gap-down skip, 8% take-profit lock).

---

## Related Project

**[TechLens-1.5B](https://github.com/Neon549/TechLens-1.5B)** — A Qwen3-1.7B model fine-tuned via SFT + DPO to serve as the local technical analyst. Eliminates cloud API latency (P50 8.5s → local) and reduces hallucinated price levels to 0% on the evaluation set. Integrated as the `TechnicalAnalyst` node with automatic DeepSeek fallback.
