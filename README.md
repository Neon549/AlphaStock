# AlphaStock

[![Backend CI](https://github.com/Neon549/Alpha_stock/actions/workflows/deploy.yml/badge.svg?branch=main)](https://github.com/Neon549/Alpha_stock/actions/workflows/deploy.yml)
[![Frontend CI](https://github.com/Neon549/Alpha_stock/actions/workflows/deploy-frontend.yml/badge.svg?branch=main)](https://github.com/Neon549/Alpha_stock/actions/workflows/deploy-frontend.yml)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Node 20](https://img.shields.io/badge/Node.js-20-339933?logo=node.js&logoColor=white)](https://nodejs.org/)

> An evidence-governed A-share research assistant. AlphaStock combines market evidence, document and news RAG, historical backtesting, and human publication controls into one auditable product. It produces research drafts and strategy suggestions; it does **not** connect to brokers or place orders.

**[Live demo](https://alphastock.cloud)** · **[API docs](https://alphastock.cloud/docs)** · **[Evaluation guide](evaluation/README.md)** · **[Harness design](agent_runtime/harness/README.md)** · **[Remote MCP guide](MCP_REMOTE.md)**

## Why AlphaStock

Investment research assistants should be able to say *what evidence was used*, *how fresh it is*, and *why a draft was blocked*. AlphaStock is built around that requirement:

- **Evidence before conclusion** — market quotes, financial indicators, and bounded daily history are stored as typed, append-only `market_evidence` records with timestamps, provenance, quality state, content hashes, and links to their originating tool result.
- **Controlled agent runtime** — one unified `AlphaStockHarness` runs business profiles rather than separate role-specific harnesses. Its registry, sandbox, checkpoints, retries, circuit breakers, and recovery rules are shared across research and investment workflows.
- **Retrieval with guardrails** — document and news retrieval use lexical and vector signals, RRF fusion, entity/time checks, citations, and a set-preserving BGE reranker fallback.
- **Reviewable output** — the output gate checks evidence, risk language, and citations before a research draft can enter the publication-review path.
- **No hidden trading capability** — raw shell, file-write/delete, publishing, and broker-trading tools are not exposed by the agent harness, including in the most permissive user mode.

## Features

| Area | Current capability |
| --- | --- |
| Research chat | Authenticated conversation with intent routing, governed tool selection, safe trace summaries, citations, and evidence cards. |
| A-share analysis | Fundamental, technical, sentiment, and risk-oriented research paths; live market evidence is explicitly timestamped and freshness-checked. |
| Document & news RAG | Session-isolated document ingestion (PDF, Word, text, CSV, Excel), page-aware citations, stock-scoped news retrieval, BM25 + pgvector + RRF, and BGE reranking. |
| Backtesting & screening | Bounded historical backtests for KDJ/MACD, RSI, and Bollinger strategies, plus research-candidate screening and Alpha-factor endpoints. |
| Long-term memory | Candidate memories are risk-routed through `safe`, `assist`, and expiring `full_access` modes; only approved Markdown is eligible for indexing. |
| Publication governance | Evidence/citation/risk checks can block a draft or route it to the configured independent-reviewer and requester-confirmation flow. |
| Observability | Per-run lifecycle, tool references, evidence status, retry/fallback state, and redacted Langfuse telemetry. |
| Remote integration | A guarded Streamable HTTP MCP endpoint exposes only bounded research operations through the same gateway and policy boundary. |

## Architecture

```mermaid
flowchart LR
    U["User / MCP client"] --> API["FastAPI API\nAuth · CORS · request guard"]
    API --> CP["Control plane\nIntent · events · run lifecycle"]
    CP --> H["AlphaStockHarness\ncheckpoint · recovery · telemetry"]
    H --> P["Profiles\nResearch · Investment"]
    P --> G["Tool gateway + sandbox\nregistered read-only capabilities"]
    G --> D["Market / documents / news / backtest"]
    D --> E["Evidence stores\nPostgreSQL + pgvector"]
    E --> O["Output gate\nevidence · citation · risk checks"]
    O --> R["Review path or blocked result"]

    classDef user fill:#EDE9FE,stroke:#7C3AED,color:#1F2937;
    classDef runtime fill:#DBEAFE,stroke:#2563EB,color:#1F2937;
    classDef evidence fill:#DCFCE7,stroke:#16A34A,color:#1F2937;
    classDef governance fill:#FEF3C7,stroke:#D97706,color:#1F2937;
    class U user;
    class API,CP,H,P,G runtime;
    class D,E evidence;
    class O,R governance;
```

The runtime starts each request with an authenticated actor and a bounded execution profile. Tool results are persisted as evidence artifacts; the public response exposes a safe `trace_summary`, while raw prompts and detailed tool payloads remain in private audit storage.

### Unified harness

```text
agent_runtime/harness/
├── run.py        Runtime kernel and run handle
├── state.py      Append-only events, checkpoints, logical rollback
├── store.py      PostgreSQL snapshots with atomic local fallback
├── recovery.py   Resume, rollback, retry, and terminal state handling
├── tools.py      Capability check, tool retries, evidence references
├── sandbox.py    Profile allowlist and fail-closed policy
├── evidence.py   Compact evidence-reference management
└── profiles.py   Research and investment manifests
```

Research and investment are profiles with different step budgets and registered tools, not separate runtime platforms. Existing workflow and LangGraph adapters remain compatibility paths for comparison and rollback.

## Technology stack

| Layer | Technology |
| --- | --- |
| Product UI | React 18, Vite 5, React Router, Zustand, Chart.js |
| API | Python 3.11, FastAPI, Pydantic, Uvicorn |
| Runtime | Custom unified Harness, Gateway/Control Plane, LangGraph compatibility adapters |
| Models | DeepSeek primary routing, Qwen fallback and multimodal support, optional TechLens service for technical analysis |
| Retrieval | BM25, PostgreSQL + pgvector, reciprocal-rank fusion (RRF), `BAAI/bge-reranker-v2-m3` cross-encoder |
| Data & analysis | AKShare, optional Tushare, pandas, backtrader, quantstats |
| Document processing | PyMuPDF, pdfplumber, python-docx, MinerU |
| Storage | PostgreSQL 17 + pgvector, SQLite only for local publication-review records |
| Observability | Langfuse (optional), PostgreSQL execution and evidence audit records |
| Delivery | Docker Compose for local pgvector, GitHub Actions, Nginx deployment |

## Repository layout

```text
.
├── api/                 FastAPI routers, authentication, uploads, reviews
├── agent_runtime/       Harness, profiles, workflows, memory, skills, MCP server
├── control_plane/       Event routing, gateway, run persistence, governance
├── market/              Structured market-evidence model and persistence
├── rag/                 News/document retrieval and reranking
├── backtest/            Historical strategies, screening, and reporting
├── evaluation/          Dataset contracts, offline evaluation, release gates
├── frontend/react-app/  React + Vite application
├── scripts/             Index maintenance, smoke clients, local utilities
├── tests/               Unit, workflow, integration, and governance tests
└── .github/workflows/   Backend and frontend CI/CD workflows
```

## Quick start

### Prerequisites

- Python 3.11
- Node.js 20 (for the frontend)
- Docker Desktop or a reachable PostgreSQL instance with the `vector` extension
- A DeepSeek API key for live agent execution

### 1. Clone and prepare Python

```bash
git clone https://github.com/Neon549/Alpha_stock.git
cd Alpha_stock

python -m venv .venv
# PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Start the local pgvector database

```bash
docker compose -f docker-compose.pgvector.yml up -d
```

Copy `.env.pgvector.example` to the ignored `.env.pgvector` file, or set `POSTGRES_DSN` yourself. Then create a local `.env` file (never commit it):

```dotenv
# Required for live agent execution
DEEPSEEK_API_KEY=replace_me

# Recommended for fallback / vision / retrieval evaluation
DASHSCOPE_API_KEY=replace_me

# Optional market-data provider for paths that use Tushare
TUSHARE_TOKEN=replace_me

# Use this instead of .env.pgvector when connecting to an existing database
# POSTGRES_DSN=postgresql://user:password@127.0.0.1:5432/alphastock

# Optional tracing
# LANGFUSE_PUBLIC_KEY=replace_me
# LANGFUSE_SECRET_KEY=replace_me
# LANGFUSE_HOST=https://cloud.langfuse.com
```

The application initializes its additive PostgreSQL schema at startup:

```bash
uvicorn main:app --reload
```

Open <http://localhost:8000/docs> for the local OpenAPI interface and call <http://localhost:8000/api/v1/health> to check startup status.

### 3. Start the frontend

```bash
cd frontend/react-app
npm ci
npm run dev
```

Vite proxies `/api` to `http://localhost:8000` in development. Production builds are emitted to `frontend/react-app/dist/`:

```bash
npm run build
npm run preview
```

## Configuration

| Variable | Required | Purpose |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | Live agent runs | Primary language-model provider. |
| `POSTGRES_DSN` | Persistent deployment | PostgreSQL connection; local Docker defaults are supplied by `.env.pgvector`. |
| `DASHSCOPE_API_KEY` | Optional | Qwen fallback, multimodal analysis, and compatible evaluation embeddings. |
| `TUSHARE_TOKEN` | Optional | Enables Tushare-backed market-data paths. |
| `TECHLENS_BASE_URL` | Optional | URL of the separately deployed technical-analysis model. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | Optional | Langfuse tracing. Failure to connect does not stop the API. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` | Optional | Google OAuth. The backend verifies Google tokens; it does not trust client profile claims. |
| `PUBLICATION_REVIEWER_USERS` | Review-enabled deployment | Comma-separated independent reviewer allowlist. |
| `ALPHASTOCK_CORS_ORIGINS` | Production | Explicit comma-separated browser origins. |
| `ALPHASTOCK_SANDBOX_NETWORK=deny` | Optional | Disables registered network-backed research tools during an incident. |

## API overview

Interactive research, backtest, upload, review, and run-diagnostics endpoints require an authenticated `Authorization: Bearer <token>` or `X-Auth-Token` header. Use the OpenAPI page for request schemas and current response models.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/health` | API, business-router, and news-index readiness. |
| `POST /api/v1/auth/register`, `/auth/login`, `/auth/logout` | Local account lifecycle. |
| `POST /api/v1/auth/google/token` | Server-verified Google token login when configured. |
| `POST /api/v1/chat` | Governed research conversation. |
| `POST /api/v1/analyze` | Evidence-aware stock analysis draft. |
| `GET /api/v1/runs/{run_id}` | Authenticated run diagnostics, steps, and evidence status. |
| `GET /api/v1/stocks/evidence/{stock_code}` | Structured quote, financial, or history evidence snapshots. |
| `POST /api/v1/backtest` | Bounded historical strategy backtest. |
| `POST /api/v1/upload/document` | Session-owned document ingestion for RAG. |
| `GET /api/v1/skills` | Active public skill metadata. |
| `/api/v1/mcp/` | Guarded Streamable HTTP MCP endpoint. |

Example:

```bash
curl http://localhost:8000/api/v1/health

curl -X POST http://localhost:8000/api/v1/analyze \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"stock_code":"600519"}'
```

## Governance and safety

### Evidence and publication

- **Market evidence:** `quote`, `financial_indicator`, and `daily_history` records have a retrieval time, reporting/as-of time where available, source, quality state, JSON payload hash, and `result_ref`. Stale or missing timestamps are marked rather than silently treated as current.
- **RAG evidence:** uploaded documents are session-owned; document citations are returned only when source support exists. News retrieval performs entity verification before BGE reranking. If the reranker is unavailable, the original bounded candidate set is retained.
- **Output gate:** unsupported investment claims, absent traceable evidence, invalid citations, and risky language can produce a blocked draft rather than an unsupported conclusion.
- **Publication review:** when review is required, the system writes a local SQLite review record, requires a configured independent reviewer, then requires final confirmation by the original requester before persisting the approved research decision. This is publication governance, not broker execution.

### Three memory-approval modes

| Mode | Behavior |
| --- | --- |
| `safe` | Keeps every long-term-memory candidate pending for explicit review. |
| `assist` | Automatically accepts low-risk operating lessons; groups the rest for batch confirmation. |
| `full_access` | Requires explicit, expiring acknowledgement; may auto-handle low/medium-risk operating lessons, while hard-blocked and high-risk content remains protected. |

Approved candidates are rendered as Markdown under `agent_runtime/memory/knowledge/`; only `status: approved` content can be indexed. Run `python scripts/sync_memory_index.py` to synchronize approved memory into the retrieval index.

`full_access` does **not** create raw command, arbitrary filesystem, publishing, or trading access. The harness enforces a profile allowlist and immutable denials for those side effects in every mode.

## Quality and evaluation

Run the deterministic offline regression suite used by CI:

```powershell
$env:ALPHASTOCK_SKIP_DOTENV='1'
$env:ALPHASTOCK_OFFLINE_TESTS='1'
python -m pytest -q tests
```

For an evaluation-focused local report, see the versioned dataset and claim controls in [`evaluation/README.md`](evaluation/README.md). The project keeps smoke fixtures, candidate corpora, external benchmarks, and production-admission data separate; retrieval metrics, RAGAS scores, and answer correctness are not treated as interchangeable claims.

The current news path uses entity-verified BM25 candidates plus a locally cached BGE Cross-Encoder (`BAAI/bge-reranker-v2-m3`) that only reorders the existing Top-5 set. Wider candidate-pool experiments and their limitations are documented in [`evaluation/BGE_NEWS_RERANK_REMOTE_REPORT.md`](evaluation/BGE_NEWS_RERANK_REMOTE_REPORT.md).

## CI/CD

Two GitHub Actions workflows run from this single repository:

- **Backend:** `.github/workflows/deploy.yml` installs `requirements-ci.txt`, runs the offline test suite, then deploys `main` after success.
- **Frontend:** `.github/workflows/deploy-frontend.yml` runs `npm ci && npm run build` for pull requests that change `frontend/`, and deploys the built site after a successful push to `main`.

The deployment workflows require repository secrets named `SERVER_HOST`, `SERVER_USER`, and `NEON_ALPHA`. Keep those values in GitHub Actions secrets only; never add them to `.env` files committed to the repository.

## Documentation

- [Unified Harness](agent_runtime/harness/README.md) — runtime kernel, persistence fallback, recovery, and sandbox contract.
- [Evaluation guide](evaluation/README.md) — dataset integrity, release-quality gates, RAG evaluation, and benchmark claim boundaries.
- [Remote MCP](MCP_REMOTE.md) — supported tools, scopes, deployment settings, and smoke client.
- [Control plane](control_plane/README.md) — event lifecycle and runtime ownership.
- [Agent learning](agent_learning/README.md) — evaluation-driven learning artifacts and review boundaries.

## Limitations

- Market data and model responses can be delayed, incomplete, or unavailable. A timestamped evidence record is not a guarantee of correctness.
- Backtests are historical simulations; users must choose sensible windows, fees, slippage, benchmarks, and out-of-sample validation before interpreting results.
- The repository is an A-share research product, not a brokerage, investment adviser, or order-management system. Nothing generated by AlphaStock is an instruction to buy or sell securities.
- Evaluation reports contain explicitly scoped engineering measurements. Do not promote a candidate or benchmark metric into a production-quality claim without its documented review protocol.

## Contributing

Issues and pull requests are welcome. Please keep changes small, include tests for behavior changes, and run the offline suite before opening a PR. Do not commit API keys, provider tokens, database URLs, uploaded documents, or generated runtime artifacts.

## License

No open-source license has been selected for this repository yet. Until a license is added, treat the code as **all rights reserved** and request permission before reuse or redistribution.
