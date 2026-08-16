# AlphaStock 智能投研助手

[![后端构建](https://github.com/Neon549/Alpha_stock/actions/workflows/deploy.yml/badge.svg?branch=main)](https://github.com/Neon549/Alpha_stock/actions/workflows/deploy.yml)
[![前端构建](https://github.com/Neon549/Alpha_stock/actions/workflows/deploy-frontend.yml/badge.svg?branch=main)](https://github.com/Neon549/Alpha_stock/actions/workflows/deploy-frontend.yml)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Node.js 20](https://img.shields.io/badge/Node.js-20-339933?logo=node.js&logoColor=white)](https://nodejs.org/)

> AlphaStock 是一个以证据治理为核心的 A 股智能投研助手。它把市场证据、文档与新闻 RAG、历史回测和人工发布控制整合为一个可审计产品，输出研究草稿和策略建议；它**不连接券商，也不会下达交易指令**。

**[在线体验](https://alphastock.cloud)** · **[接口文档](https://alphastock.cloud/docs)** · **[评测说明](evaluation/README.md)** · **[统一 Harness 设计](agent_runtime/harness/README.md)** · **[远程 MCP 说明](MCP_REMOTE.md)**

## 项目定位

投研助手不应只给出结论，还应能够说明“使用了哪些证据”“证据是否新鲜”“为什么结论被阻断”。AlphaStock 围绕这一目标构建：

- **先证据，后结论**：行情、财务指标和有限窗口的日线历史会保存为结构化、仅追加的 `market_evidence` 记录，包含时间戳、来源、质量状态、内容哈希及原始工具结果引用。
- **统一智能体运行时**：研究与投资不是各自独立的运行器，而是由同一个 `AlphaStockHarness` 按业务配置执行；工具注册表、沙箱、断点、重试、熔断、恢复和审计逻辑统一复用。
- **受控检索**：文档与新闻检索结合词法检索、向量检索、RRF 融合、实体与时间校验、引用，以及保守的 BGE 重排回退机制。
- **可审查输出**：输出门会先检查证据、风险措辞和引用，再决定将研究草稿阻断或送入发布审核流程。
- **不存在隐藏交易能力**：无论用户模式为何，智能体运行器都不暴露原始 Shell、文件写入或删除、发布、券商交易等工具。

## 已实现功能

| 模块 | 当前能力 |
| --- | --- |
| 投研对话 | 认证后的智能对话，包含意图路由、受控工具选择、安全运行摘要、引用和证据卡片。 |
| A 股分析 | 基本面、技术面、情绪面与风险导向的研究路径；实时市场证据带有明确时间戳并进行新鲜度校验。 |
| 文档与新闻 RAG | 按会话隔离的文档接入（PDF、Word、文本、CSV、Excel）、页码引用、股票范围新闻检索、BM25 + pgvector + RRF 以及 BGE 重排。 |
| 回测与筛选 | KDJ/MACD、RSI、布林带等受限历史回测，以及研究候选筛选和 Alpha 因子接口。 |
| 长期记忆 | 长期记忆候选通过 `safe`、`assist`、带时效的 `full_access` 三种模式进行风险路由；仅已批准的 Markdown 文档可以进入索引。 |
| 发布治理 | 输出门可阻断不具备证据、引用或风险要求的草稿；需要发布审核时，进入独立审核人与请求者确认流程。 |
| 可观测性 | 记录单次运行生命周期、工具引用、证据状态、重试/回退状态，以及经脱敏的 Langfuse 追踪信息。 |
| 远程集成 | 通过受保护的流式 HTTP MCP 端点提供有限的研究能力，仍复用同一个网关和权限边界。 |

## 系统架构

![AlphaStock 统一投研运行流程](docs/architecture.svg)

每次运行从经过认证的主体和受限的执行配置开始。工具结果会被持久化为证据工件；公开接口只返回安全的 `trace_summary`，原始提示词和详细工具载荷保留在私有审计存储中。

### 统一 Harness

```text
agent_runtime/harness/
├── run.py        运行内核与运行句柄
├── state.py      仅追加事件、断点和逻辑回滚
├── store.py      PostgreSQL 快照与原子化本地回退
├── recovery.py   续跑、回滚、重试和终态处理
├── tools.py      能力校验、工具重试和证据引用
├── sandbox.py    Profile 白名单与故障关闭策略
├── evidence.py   精简证据引用管理
└── profiles.py   研究与投资工具清单
```

研究与投资仅是拥有不同步数预算和工具清单的业务配置，不是两套运行时平台。既有固定工作流和 LangGraph 适配器保留为兼容、比较和回退路径。

## 技术栈

| 层级 | 技术方案 |
| --- | --- |
| 产品前端 | React 18、Vite 5、React Router、Zustand、Chart.js |
| 后端接口 | Python 3.11、FastAPI、Pydantic、Uvicorn |
| Agent 运行时 | 自研统一 Harness、Gateway/控制平面、LangGraph 兼容适配器 |
| 模型 | DeepSeek 主模型路由、Qwen 回退与多模态支持、可选的 TechLens 技术分析服务 |
| 检索 | BM25、PostgreSQL + pgvector、倒数排序融合（RRF）、`BAAI/bge-reranker-v2-m3` 交叉编码器 |
| 数据与分析 | AKShare、可选 Tushare、pandas、backtrader、quantstats |
| 文档处理 | PyMuPDF、pdfplumber、python-docx、MinerU |
| 数据存储 | PostgreSQL 17 + pgvector；SQLite 仅用于本地发布审核记录 |
| 可观测性 | 可选 Langfuse、PostgreSQL 运行与证据审计记录 |
| 交付 | Docker Compose 本地 pgvector、GitHub Actions、Nginx |

## 仓库结构

```text
.
├── api/                 FastAPI 路由、认证、上传和审核
├── agent_runtime/       Harness、Profile、工作流、记忆、技能、MCP 服务
├── control_plane/       事件路由、网关、运行持久化和治理
├── market/              结构化市场证据模型与持久化
├── rag/                 新闻/文档检索与重排
├── backtest/            历史策略、筛选和报告
├── evaluation/          数据集约束、离线评测和发布门禁
├── frontend/react-app/  React + Vite 前端
├── scripts/             索引维护、冒烟客户端和本地工具
├── tests/               单元、工作流、集成和治理测试
└── .github/workflows/   前后端自动化工作流
```

## 本地启动

### 前置条件

- Python 3.11
- Node.js 20（用于前端）
- Docker Desktop，或可访问且已启用 `vector` 扩展的 PostgreSQL 实例
- 用于实时智能体运行的 DeepSeek API 密钥

### 1. 克隆并准备 Python 环境

```bash
git clone https://github.com/Neon549/Alpha_stock.git
cd Alpha_stock

python -m venv .venv
# Windows PowerShell：.venv\Scripts\Activate.ps1
# macOS 或 Linux：source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. 启动本地 pgvector 数据库

```bash
docker compose -f docker-compose.pgvector.yml up -d
```

将 `.env.pgvector.example` 复制为已被 Git 忽略的 `.env.pgvector`，或自行设置 `POSTGRES_DSN`。然后创建本地 `.env` 文件（请勿提交）：

```dotenv
# 实时智能体运行必填
DEEPSEEK_API_KEY=replace_me

# 推荐：用于回退模型、视觉能力和检索评测
DASHSCOPE_API_KEY=replace_me

# 可选：启用依赖 Tushare 的市场数据路径
TUSHARE_TOKEN=replace_me

# 连接现有数据库时，可替代 .env.pgvector
# POSTGRES_DSN=postgresql://user:password@127.0.0.1:5432/alphastock

# 可选追踪
# LANGFUSE_PUBLIC_KEY=replace_me
# LANGFUSE_SECRET_KEY=replace_me
# LANGFUSE_HOST=https://cloud.langfuse.com
```

应用会在启动时执行增量 PostgreSQL 建表：

```bash
uvicorn main:app --reload
```

打开 <http://localhost:8000/docs> 查看本地 OpenAPI 文档，并访问 <http://localhost:8000/api/v1/health> 检查启动状态。

### 3. 启动前端

```bash
cd frontend/react-app
npm ci
npm run dev
```

开发环境下，Vite 会将 `/api` 代理到 `http://localhost:8000`。生产构建产物输出至 `frontend/react-app/dist/`：

```bash
npm run build
npm run preview
```

## 配置项

| 变量 | 是否必填 | 作用 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 实时智能体运行 | 主语言模型提供方。 |
| `POSTGRES_DSN` | 持久化部署 | PostgreSQL 连接；本地 Docker 默认值由 `.env.pgvector` 提供。 |
| `DASHSCOPE_API_KEY` | 可选 | Qwen 回退、多模态分析和兼容的评测嵌入模型。 |
| `TUSHARE_TOKEN` | 可选 | 启用 Tushare 市场数据路径。 |
| `TECHLENS_BASE_URL` | 可选 | 独立部署的技术分析模型服务地址。 |
| `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_HOST` | 可选 | Langfuse 运行追踪；连接失败不会阻止 API 启动。 |
| `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`GOOGLE_REDIRECT_URI` | 可选 | Google OAuth；后端会验证 Google Token，不信任前端直接提交的用户资料。 |
| `PUBLICATION_REVIEWER_USERS` | 启用发布审核时 | 逗号分隔的独立审核人白名单。 |
| `ALPHASTOCK_CORS_ORIGINS` | 生产环境 | 显式配置的浏览器来源列表。 |
| `ALPHASTOCK_SANDBOX_NETWORK=deny` | 可选 | 事故期间关闭已注册的网络型研究工具。 |

## 接口概览

交互式投研、回测、上传、审核和运行诊断接口要求通过 `Authorization: Bearer <token>` 或 `X-Auth-Token` 认证。完整请求结构和响应模型以 OpenAPI 页面为准。

| 接口 | 用途 |
| --- | --- |
| `GET /api/v1/health` | 查看 API、业务路由和新闻索引的就绪状态。 |
| `POST /api/v1/auth/register`、`/auth/login`、`/auth/logout` | 本地账号生命周期。 |
| `POST /api/v1/auth/google/token` | 配置完成后，由后端验证 Google Token 的登录入口。 |
| `POST /api/v1/chat` | 受治理的投研对话。 |
| `POST /api/v1/analyze` | 生成带证据意识的股票分析草稿。 |
| `GET /api/v1/runs/{run_id}` | 查看已认证用户的运行诊断、步骤和证据状态。 |
| `GET /api/v1/stocks/evidence/{stock_code}` | 获取结构化行情、财务或历史证据快照。 |
| `POST /api/v1/backtest` | 运行受限的历史策略回测。 |
| `POST /api/v1/upload/document` | 将用户拥有的会话文档接入 RAG。 |
| `GET /api/v1/skills` | 查看当前公开技能元数据。 |
| `/api/v1/mcp/` | 受保护的 Streamable HTTP MCP 端点。 |

示例：

```bash
curl http://localhost:8000/api/v1/health

curl -X POST http://localhost:8000/api/v1/analyze \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"stock_code":"600519"}'
```

## 治理与安全边界

### 证据与发布

- **市场证据**：`quote`、`financial_indicator` 和 `daily_history` 带有获取时间、可用时点/报告期、来源、质量状态、JSON 载荷哈希与 `result_ref`。时间缺失或陈旧的记录会被显式标记，不能被悄悄当作当前事实。
- **RAG 证据**：上传文档按会话归属隔离；只有具备来源支撑时才返回文档引用。新闻在 BGE 重排前先校验股票实体；重排器不可用时，保留原有的受限候选集。
- **输出门**：无证据的投资主张、无效引用和高风险措辞会产生阻断结果，而不是生成无法追溯的结论。
- **发布审核**：需要审核时，系统写入本地 SQLite 审核记录，要求已配置的独立审核人先处理，再由原始请求者完成最终确认，之后才保存已批准的研究决策。这是发布治理，不是交易执行。

### 三种长期记忆审核模式

| 模式 | 行为 |
| --- | --- |
| `safe` | 所有长期记忆候选保持待审核，等待用户显式处理。 |
| `assist` | 自动接受低风险操作经验，其余候选合并为批量确认。 |
| `full_access` | 需要显式且会过期的风险确认；可自动处理低/中风险操作经验，硬阻断内容和高风险内容仍受保护。 |

已批准候选会渲染为 `agent_runtime/memory/knowledge/` 下的 Markdown；只有 `status: approved` 的内容可被索引。使用 `python scripts/sync_memory_index.py` 将已批准记忆同步到检索索引。

`full_access` **不会**产生原始命令、任意文件系统、发布或交易权限。统一运行器始终对这些副作用执行业务配置白名单和不可绕过的拒绝策略。

## 质量与评测

运行自动化构建使用的确定性离线回归测试：

```powershell
$env:ALPHASTOCK_SKIP_DOTENV='1'
$env:ALPHASTOCK_OFFLINE_TESTS='1'
python -m pytest -q tests
```

评测数据集版本、发布质量门禁、RAG 评测和指标声明边界见 [`evaluation/README.md`](evaluation/README.md)。项目明确区分冒烟样例、候选语料、外部基准和可进入生产质量声明的数据；检索指标、RAGAS 分数和答案正确性不能互相替代。

当前新闻路径使用实体校验后的 BM25 候选集，再使用本地缓存的 BGE 交叉编码器（`BAAI/bge-reranker-v2-m3`）对既有前 5 条结果进行重排。更大候选池的离线实验和适用边界记录在 [`evaluation/BGE_NEWS_RERANK_REMOTE_REPORT.md`](evaluation/BGE_NEWS_RERANK_REMOTE_REPORT.md)。

## 自动化构建与部署

本单仓库包含两条 GitHub Actions 工作流：

- **后端**：`.github/workflows/deploy.yml` 安装 `requirements-ci.txt`、运行离线测试，并在 `main` 分支通过后部署。
- **前端**：`.github/workflows/deploy-frontend.yml` 在修改 `frontend/` 的拉取请求中执行 `npm ci && npm run build`，并在 `main` 分支推送成功后部署构建产物。

部署工作流使用名为 `SERVER_HOST`、`SERVER_USER` 和 `NEON_ALPHA` 的 GitHub Actions Secrets。请仅在 GitHub Secrets 中保存这些值，绝不能将其写入并提交 `.env` 文件。

## 延伸文档

- [统一 Harness](agent_runtime/harness/README.md)：运行内核、持久化回退、恢复与沙箱契约。
- [评测说明](evaluation/README.md)：数据集完整性、发布质量门禁、RAG 评测和基准声明边界。
- [远程 MCP](MCP_REMOTE.md)：已支持工具、权限范围、部署变量与冒烟客户端。
- [控制平面](control_plane/README.md)：事件生命周期和运行时归属。
- [Agent 学习](agent_learning/README.md)：评测驱动的学习工件与审核边界。

## 当前限制

- 市场数据和模型响应可能延迟、不完整或不可用。带时间戳的证据记录不代表其必然正确。
- 回测是历史模拟；在解读结果前，仍应自行设置合理的时间窗口、费率、滑点、基准和样本外验证。
- 本仓库是 A 股研究产品，不是券商、投资顾问或订单管理系统。AlphaStock 输出的任何内容都不是证券买卖指令。
- 评测报告只包含有明确范围的工程测量。没有完成其文档规定的审核流程前，不得将候选集或基准指标宣传为生产质量结论。

## 参与贡献

欢迎提交问题反馈和代码改动。请保持改动聚焦；修改行为时补充测试；提交代码前运行离线测试。请勿提交 API 密钥、供应商令牌、数据库连接串、上传文档或生成的运行时文件。

## 许可证

当前仓库尚未选择开源许可证。在添加许可证前，代码应视为**保留所有权利**；复用或再分发前请先取得许可。
