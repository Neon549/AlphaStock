# Control Plane：触发、网关与运行时边界

```text
HTTP / CLI / Cron / Webhook / Hook
              │
              ▼
       AgentEvent（统一事件）
              │
              ▼
 Gateway（去重、路由；不调模型、不执行工具）
              │
              ▼
 InvestmentRuntime（意图、Memory、Skill、工作流选择）
          ┌───┴────────────────────┐
          ▼                        ▼
  直接回复 / 追问          固定投研工作流适配器
                                    │
                                    ▼
                 Python State Machine + ResearchHarness
                                    │
                                    ▼
                          Governance / HITL / Trace
```

## 当前已接入

- `/chat` 与 `/analyze` 是 HTTP trigger adapters：请求被转换为 `AgentEvent` 后交给 `Gateway`。
- `InvestmentRuntime` 在 intent=1/3/4 时直接返回，不启动重型分析；intent=2 才选择策略知识、文档 RAG 并调用固定投研工作流。
- `PythonInvestmentRuntime` 是当前默认固定投研工作流；其节点实现位于 `agent_runtime/workflows/investment_handlers.py`。`LangGraphInvestmentRuntime` 只保留为可选兼容适配器，用于交叉运行时对照与紧急回滚。
- `backtest/service.py` 是 API、LangChain tool 与运行时共用的单一回测执行服务。`PythonBacktestRuntime` 是 CLI 和“分析 + 回测”组合流程的默认回测运行时：固定执行回测、解读、参数优化三步；原始回测失败时跳过优化，避免产生误导性补全结果。LangGraph 回测图调用同一批 shared handlers。
- Researcher 节点中的 `ResearchHarness` 仍是受限的自主循环：只能在允许的只读工具中选择，且有工具次数、结果长度、权限和审计 trace 限制。
- PostgreSQL 的 `agent_events`、`agent_runs`、`agent_steps` 已记录事件、路由结果和不含原始报告/Prompt 的执行步骤；`event_id` 是跨进程幂等键。
- `agent_session_transcript` 自动追加用户/助手回合，作为完整会话记录；每轮最多取最近 8 条、总共 2,400 字符回灌。`agent_session_memory` 则保存股票、分析维度、未解决风险与证据 ID 等结构化会话摘要。
- `agent_memory_maintenance_jobs` 是独立 Background Memory Worker 的持久化队列：用户路径仅入队，定时 Worker 才能读取限定 transcript ID 区间并创建待审核候选。Worker 没有工具/MCP 权限，不能直接批准、索引、修改偏好或发布内容。
- `financial-indicators` 用显式 `报告期` 源字段选择最新财报记录；研究 Harness 将数据源、抓取时间、报告期、距今天数和新鲜度生成 UI evidence card。过期或缺失报告期的数据不能作为当前结论依据。
- `user_preferences` 仅保存用户经认证后显式提交的风险偏好、关注行业、观察列表和回答风格。
- `memory_context` 被作为低优先级、非证据 ContextBlock 注入最终解释阶段。它不能覆盖实时数据、改变事实结论，未审核草案也不会进入长期决策记忆。
- `ContextWindowBuilder` 每轮按 profile 拼装 Bootstrap、Skill 简介、用户偏好、会话摘要、最近 Transcript 与当前请求；Research Harness 使用 `research` profile，开放讨论使用 `discussion` profile。总预算 6,000 token，70% 起剔除低优先级块、85% 硬上限；请求本身过长会被阻断而非静默截断。工具 observation 会先 microcompact 为证据 ID、来源、新鲜度和短预览，Provider 413 时仅做一次 emergency compact 重试。数据采集 Analyst 不携带对话历史，以免历史叙述污染当期证据判断。

## 有意没有照搬的部分

- 没有接 Telegram、Discord、Email：项目目前只有 Web/CLI；先有统一事件契约，未来新增 channel 不必触碰 Agent 代码。
- Gateway 同时做进程内去重和 PostgreSQL `event_id` 唯一约束；HTTP 的 `/chat`、`/analyze` 已支持 `Idempotency-Key`。同进程重试直接复用已完成结果；重启后的重复请求返回 HTTP 409，后续可增加 `GET /runs/{run_id}` 读取持久化结果。
- HTTP 鉴权仍在 FastAPI auth middleware/route；Gateway 不应拥有用户登录逻辑。
- `fast/smart/strong` 已通过 `ModelProfile + ContextVar` 做到每个 Run 独立的模型选择；运行时用 `model_scope()` 注入，避免并发请求修改模块级全局模型配置。
- 没有让 "Reflect" 自动改 Prompt、Memory 或 Skill。投研场景的复盘应先生成候选经验，再通过离线评测和人工审批后版本化写入 Skill/规则，避免线上自我污染。

## 后续迁移顺序

1. 主流程（600519 technical）和回测（600519/kdj_macd）已完成跨运行时真实对照：稳定安全契约一致。下一步补入已脱敏真实文档 fixture，完成真实文档 RAG 证据链对照；`compat/langgraph/` 在此之前保留为紧急回滚适配层。
2. 扩充长期经验 Markdown 与专项检索评测集；索引只负责定位，真正进入 Context 的永远是命中的原文片段。
3. 在 `agent_runs` 记录开始、取消和恢复状态，而不是只有完成态。
4. `compat/langgraph/trading_graph.py` 已收敛为薄适配层；后续只维护其与 Python Runtime 的稳定契约，不再新增业务节点实现。
