# AlphaStock · A-Share Intelligent Research Assistant

> Multi-agent stock analysis system combining fundamental, technical, and sentiment analysis with quantitative backtesting to support trading decisions.

🌐 **Live Demo**: [alphastock.cloud](https://alphastock.cloud) · 📦 **Backend**: [Neon549/Alpha_stock](https://github.com/Neon549/Alpha_stock) · 🖥️ **Frontend**: [Neon549/Alpha_stock_frontend](https://github.com/Neon549/Alpha_stock_frontend)

---

## What It Does

| Feature | Description |
|---|---|
| **Stock Analysis** | Input a ticker — three parallel agents (fundamental / technical / sentiment) debate and output a long/short verdict with position sizing |
| **Quantitative Backtest** | KDJ+MACD / RSI / Bollinger Band strategies with grid-search parameter optimization (36 combinations); outputs Sharpe ratio, max drawdown, win rate |
| **News Sentiment** | Real-time A-share news retrieval via BM25 + pgvector + RRF hybrid search; sentiment score feeds directly into the trading decision |
| **Buy Signal Screener** | Daily scan across 2,000 stocks for KDJ oversold + golden-cross signals; alpha factor scoring (5-factor model, score ≥ 85 = priority watchlist) |
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
| Long-term Memory | PostgreSQL (conversation persistence) |
| Observability | LangFuse v2 (full LLM trace via Docker) |
| Auth | Google OAuth / GitHub OAuth / Email verification (Aliyun Direct Mail) |
| Backend API | FastAPI |
| Frontend | React 18 + Vite ([Neon549/Alpha_stock_frontend](https://github.com/Neon549/Alpha_stock_frontend)) |
| Deployment | Tencent Cloud · Nginx · Let's Encrypt SSL · UptimeRobot |

---

## Key Design Decisions

### Hallucination Control
- Ticker names resolved against a local dictionary of 1,500+ A-share stocks — never inferred by the LLM
- Every analyst output enforces `[ANALYSIS_OK]` / `[ANALYSIS_ABORT]` tagging; malformed or unsupported outputs are blocked before reaching downstream nodes
- Tool layer validates all returned data types before they enter the LangGraph state

### Hybrid Retrieval (RAG)
BM25 handles exact-match terms (ticker codes, indicator names); pgvector handles semantic similarity; RRF merges both ranked lists without manual weight tuning.

Ablation results across four retrieval strategies (Recall@10):

| Strategy | Recall@10 |
|---|---|
| BM25 only | 0.8448 |
| Dense vector only | 0.8362 |
| Simple weighted mix | ~0.79 |
| **Hybrid + RRF** | **0.8707** ✅ |

### Structured Query Understanding

Intent routing uses three layers: deterministic rules first, then a high-confidence local fastText four-class classifier (`discussion` / `analysis` / `system` / `insufficient`), and finally the LLM JSON parser for low-confidence or unfamiliar inputs. For an analysis intent, fastText only returns directly when the stock name can be resolved by the local mapping; otherwise the request falls back to the LLM so stock and analyst-focus slots are not lost. The `analysts_node` reads `analyst_focus` and skips irrelevant analysts (returning `[SKIPPED]`).

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

---

## RAG Evaluation

Full report: [`evaluation/EVAL_REPORT.md`](evaluation/EVAL_REPORT.md)

Evaluated with **RAGAS 0.1.21** using Qwen as the Judge LLM (switched from DeepSeek after discovering DeepSeek does not support `n > 1`, causing `Answer Relevancy = nan`).

| Metric | Dense-only | Hybrid (BM25 + pgvector + RRF) |
|---|---|---|
| **Faithfulness** | 0.854 | **0.952 ✅ (+10%)** |
| Context Recall | 0.567 | 0.567 |
| Context Precision | 0.527 | 0.487 |

Development follows **EDD (Evaluation-Driven Development)**: every retrieval or prompt change runs `evaluation/evaluator.py` before merging.

Online monitoring uses one Langfuse trace per request. It records a redacted query fingerprint, corpus snapshot, top-k distances, rerank state, citation structural validation, abstention, token usage and latency. Public API responses expose only `run_id`, answer/citations and a safe `trace_summary`; prompts and detailed evidence remain in the private audit/Langfuse view. Alerting threshold: Faithfulness < 0.85 triggers review.

---

## Quick Start

```bash
git clone https://github.com/Neon549/Alpha_stock
cd Alpha_stock
pip install -r requirements.txt

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
