# AlphaStock 项目交接摘要（2026-08-03）

> 给下一个 Codex chat 的上下文。仓库：`D:\code\ProjectExample\Alpha_stock_new`。工作区存在大量**未提交**改动；不要使用 `git reset --hard`、`git checkout --` 或覆盖性回滚。

## 1. 项目目标与边界

这是面向个人投资者的 A 股投研辅助系统，而不是自动交易系统。输入股票代码或名称后，系统完成技术面、基本面、情绪面分析，补充文档/记忆/市场证据，生成带证据与风险提示的研究草案；所有投资结论必须经过 Human-in-the-loop 审核，不能自动发布或承诺收益。

技术主线：Python、FastAPI、React 前端、PostgreSQL + pgvector、AKShare、LangChain、DeepSeek/Qwen fallback、MinerU + PyMuPDF/pdfplumber/OCR 文档回退、BM25 + 向量 + RRF、Docker。上传文档、策略知识与新闻检索统一使用 PostgreSQL + pgvector；ChromaDB 已完成退役。

## 2. 当前架构（已经完成）

```text
HTTP / CLI / Cron / Webhook
          |
AgentEvent
          |
control_plane/
  Gateway: 去重、路由、Run 存储；不调 LLM、不执行工具
  InvestmentRuntime: 意图、Memory、Skill、Context、工作流选择
          |
agent_runtime/
  agents/       三分析师 + 有上限的 ResearchHarness
  context/      Token budget、状态快照、ContextWindow profiles
  memory/       session 摘要、经验候选、approved Markdown Memory Index
  skills/       registry、权限、版本、Document-RAG handler
  workflows/    默认纯 Python State Machine、治理、回测运行时
  compat/langgraph/  仅 LangGraph 对照/回滚适配层
          |
rag/ tools/ backtest/ db.py config/
          |
带证据的研究草案 -> Human review gate
```

### 运行时责任

- `control_plane/gateway.py`：统一事件入口、幂等和路由；不承担模型推理或工具调用。
- `control_plane/investment_runtime.py`：默认 `workflow_runtime="python"`。可设环境变量 `INVESTMENT_WORKFLOW_RUNTIME=langgraph` 临时切换兼容实现。
- `agent_runtime/workflows/python_state_machine.py`：主投研固定流程：`policy_guard -> analysts -> context_snapshot -> validation -> (replan?) -> researcher -> trader -> output_gate`。
- `agent_runtime/workflows/investment_handlers.py`：上述节点的框架无关实现；LangGraph 和 Python Runtime 共用。
- `backtest/service.py`：API、LangChain tool 和 Runtime 共用的唯一回测执行入口，统一数据加载、输入校验、引擎调用、格式化报告和运行时报告输出目录。
- `agent_runtime/workflows/backtest_state_machine.py`：固定回测流程：`backtest -> interpreter -> optimizer`；若 `backtest_report` 含 `[TOOL_ERROR]`，解释后直接结束，不跑优化。
- `agent_runtime/workflows/runtime.py`：`PythonInvestmentRuntime`、`PythonBacktestRuntime` 为默认；`LangGraphInvestmentRuntime`、`LangGraphBacktestRuntime` 仅兼容适配器。
- `agent_runtime/compat/langgraph/`：旧 `trading_graph.py / scan_graph.py / state.py` 已从根目录 `graph/` 搬入。`trading_graph.py` 现为薄兼容适配层，复用 shared Python handlers，仅用于跨运行时对照、checkpoint 实验和紧急回滚。

## 3. 入口与意图识别

- FastAPI 是 HTTP 交付层，不是 Gateway 本身；认证和路由在 API 层，业务事件再交给 `Gateway`。
- 意图识别在 `api/intent_parser.py`：**规则 -> 高置信度 fastText 四分类 -> LLM JSON 槽位兜底**。
- 四类 intent：`discussion / analysis / system / insufficient`。分析类只有在本地股票映射能确认代码时才允许 fastText 直接返回；否则交给 LLM 补齐 `stock_code`、`analyst_focus` 等槽位。
- CLI 存在 `analyze / backtest / workflow` 三类命令；`analyze` 走 Gateway，`backtest` 走 `PythonBacktestRuntime`，`workflow` 并行运行 Python 投研与 Python 回测后生成待审核综合草案。
- 已有 Trigger / Gateway 抽象：HTTP、CLI、Cron、Webhook、Hook 都能转换为 `AgentEvent`；目前外部实际接入以 Web/CLI 为主。

## 4. Agent、Skill 与 Research Harness

- 固定工作流不是开放式 Agent Loop；三面分析、验证、交易草案由确定性 State Machine 编排。
- `agent_runtime/agents/research_harness.py` 是局部受限 Agent Loop：LLM 只能选择只读工具，最多 `MAX_TOOL_CALLS=3` 次，每个工具结果最多 1800 字符；工具结果写入 trace，再回灌下一轮决策。
- Harness 可用工具：`document-rag`、`market-price`、`financial-indicators`、`stock-news`、`memory-search`；有权限、股票绑定、迭代次数和结果长度控制。
- Skill Registry：每个 Skill 用 `skill.json` 描述 name/description/trigger/permission/version；先按权限和触发条件过滤，再由 LLM/规则选择；`document-rag` 是 session 隔离的只读 Skill。
- 市场工具成功响应统一附带 `retrieved_at`；财务工具附带 `report_period`。Harness 将金融报告期超过 540 天标记为 `stale`，缺失报告期/获取时间也标记为不可作为当前证据；同一工具与 query 的重复调用会被跳过并写入 trace。

## 5. 上下文、Memory 与 RAG

### Context

- `ContextWindowBuilder` 按 profile 组装 Bootstrap、Skill 简介、用户偏好、session memory、最近 transcript、当前请求；6,000 token 总预算下，70% 起丢弃低优先级块、85% 为硬上限。单个当前请求本身无法安全放入时直接要求拆分，不会静默截断。
- 当前不是通用无限聊天 Agent：通过 session 隔离、Top-K 检索、结构化状态快照和 token budget 控制上下文。
- 压缩采用投研安全版五层：工具结果 1,800 字符预算 → 工具 observation microcompact（证据 ID/来源/新鲜度/短预览）→ 每 Run 结构化 session snapshot → ContextWindow 的 70%/85% 预算门槛 → Provider 413 时一次 emergency compact 重试。完整工具结果会落入 `runtime/cache/tool_results/` 并由 `result_ref` 标识；原始报告不由 LLM 自由摘要，仍按 evidence ID/source 回查。
- 分析结果应传递“结论 + 关键指标 + evidence IDs + 时间戳 + 未解决风险”，原始 PDF、工具输出和完整报告留在数据库/trace，按证据 ID 回查。
- `PythonInvestmentRuntime` 已把 Gateway 的 `doc_context` 规范化成状态字段 `user_doc_context`，所以文档证据会进入 scope 校验与 Analyst。

### Memory

- `agent_session_transcript`：完整会话日志；续聊仅尾部取最近 8 条、总计约 2400 字符。
- `agent_session_memory`：结构化 session 摘要（股票、分析维度、风险、evidence IDs），不是原始报告全文。
- `user_preferences`：用户显式提交的偏好；低优先级、非证据，不能覆盖当前市场事实。
- Memory Candidate：Bad Case、回测偏差、人工复盘可生成 `pending` 候选；人工审核后才写 approved Markdown 并索引，禁止线上自我污染。
- Memory Index：`agent_runtime/memory/knowledge/` 中只有 front matter `status: approved` 的 Markdown 可入库；标题感知切分 `600 / overlap 80`；写入 PostgreSQL `agent_memory_chunks`，使用 pgvector HNSW（m=16, ef_construction=64）；检索返回原文 chunk、hash、source path、evidence ID。经验不是实时市场证据。
- Background Memory Worker：`agent_memory_maintenance_jobs` 已提供持久化 extract/sleep job。请求完成后只在认证会话积累至少 8 个新增 turn 或 20,000 字符时入队；`scripts/run_memory_maintenance.py` 在独立进程中读取 bounded transcript range，最多生成 3 个 pending candidate。它不能调用工具/MCP、不能自动批准或索引。sleep 阶段只生成重复标题审计报告，绝不直接合并/删除 Markdown。

### 文档 RAG

- 上传 PDF/DOCX/TXT/CSV：PDF 优先 MinerU，保留 PyMuPDF/pdfplumber/OCR 回退；将内容按“章节路径 -> 子块 -> 相邻块”组织。
- 检索单元是子块；命中后用标题路径和邻块补全语境；返回文件、章节、页码/chunk evidence。
- PostgreSQL + pgvector 统一承载上传文档与部分知识向量；session 级文档量小，先按 session 过滤，当前刻意不建 HNSW。
- 检索方案：BM25 稀疏召回 + pgvector 稠密召回 + RRF。历史消融结果：BM25 Recall@10=0.8448，Dense=0.8362，Hybrid+RRF=0.8707；Cross-Encoder rerank=0.8190，因此当前保留 Hybrid+RRF。

## 6. 治理、评测和工程化

- `agent_runtime/workflows/governance.py`：校验 6 位股票代码、分析范围、文档长度（30,000 字符）与不支持的收益/确定性承诺；最终输出默认 `requires_human_review`。
- Analyst 使用 `[ANALYSIS_OK] / [ANALYSIS_ABORT]`；无数据或错误不应伪造结论。
- `control_plane/model_profile.py`：`ModelProfile + ContextVar + model_scope()`，fast/smart/strong 按 Run 隔离，避免旧全局 LLM 配置导致并发串扰。
- `evaluation/regression_runner.py` + `evaluation/datasets/workflow_regression.jsonl`：当前 8 条确定性治理回归 case；GitHub Actions PR/main 部署前执行 unittest。
- RAG 的大规模 golden set 尚未完成。已有 `evaluation/datasets/RAG_GOLDENSET.md` 作为构建规范；目标是固定语料快照 + JSONL，分别评估 pgvector/BM25/Hybrid 的 Recall@K、MRR、nDCG、citation hit rate。
- `scripts/compare_workflow_runtimes.py --execute`：会真实运行 LangGraph 与 Python 两套流程，比较发布状态、人工审核、三面状态、证据数量、风险字段和回测优化是否跳过等稳定契约；模型生成的决策类别、说明文字只作为非阻塞观察项，不做字符串一致性比较。2026-08-03 已用 600519 真实数据完成 technical 对照（两侧安全契约一致；单次执行 Python 57.555s、LangGraph 85.853s）和 `kdj_macd` 回测对照（指标、优化状态和输入契约一致；Python 7.009s、LangGraph 17.346s）。报告在 `runtime/reports/`，该目录不提交。

## 7. 已做的仓库整理

- 根目录测试已移动到 `tests/unit`、`tests/workflow`、`tests/integration`、`tests/evaluation`。
- 本地生成物移到 `runtime/cache`、`runtime/reports`、`runtime/tmp`、`runtime/checkpoints.db`，并在 `.gitignore` 忽略。
- Agent 代码已移动到 `agent_runtime/`；根目录 `graph/` 已消失。
- 历史重复 backtest snapshot 与迁移脚本被移到外部可恢复备份：`C:\Users\yulin\Documents\空闲栏目\AlphaStock-cleanup-backup-20260803-1800`。
- Chroma 已退役；不要把 `.env`、`models/`、本地数据库当作无用文件删除。

## 8. 验证命令与当前结果

```powershell
cd D:\code\ProjectExample\Alpha_stock_new
python -m unittest discover -s tests -t . -p "test_*.py"
```

当前最后一次：**68 tests passed**；React production build 也已通过。

Windows 控制台若因 emoji 日志出现 GBK 编码问题，可用：

```powershell
python -X utf8 <script.py>
```

## 9. 下一步优先级（不要重复已完成的迁移）

1. 为跨 Runtime 对照补充已脱敏的真实上传文档 fixture（当前 PostgreSQL `uploaded_document_chunks` 为 0 条）；通过真实入库、检索、证据回链后运行 `--document-context-file/--document-citations-file` 对照。主流程与回测的真实对照已完成，`compat/langgraph/` 暂作为紧急回滚适配层保留。
2. `financial-indicators` 已从“首行/首列约定”改为显式 `报告期` 字段映射，并选择最新可解析记录；真实 600519 校验得到 `2025-12-31`（旧逻辑曾错误取到 1998）。证据卡已贯通数据源、抓取时间、财报期、源字段、age_days、新鲜度与“能否作为当前结论依据”。
3. Memory Index 已定义七类 taxonomy（governance/research/retrieval/workflow/operations/backtest/evaluation）、候选→人工审核→approved Markdown→显式索引同步流程和 1200/400/400 的 2000-case split 计划；评测器已有 Recall@K、MRR、Precision@K、错误召回率和 evidence-class 违规率。下一步是持续收集真实复盘材料，先人工审核再扩容，不要批量自动批准。
4. RAG Golden Set 已有固定 fixture 语料快照、JSONL seed 和 `rag_golden_eval.py`，可评 Recall@K、MRR、nDCG、citation hit/backlink、拒答合规和无证据输出率。它只是可运行合同样例；下一步用已脱敏真实文档建立固定快照与人工 Golden Set，再分别接 `pgvector_only` / `bm25_only` / `hybrid_rrf` adapter 跑基线。
5. 如要升级为更开放的 Claude-Code 风格 Agent，只在 ResearchHarness / Tool+Skill 层做受限 loop，不要替换投研的政策门禁和固定主流程。
