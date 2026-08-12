# Notes2 对话摘要

> 用途：下一个 Chat 开始时先读取本文件。以下内容以 `Alpha_stock_new` 项目实际代码为准；凡是“建议/规划”会明确标注，不能把尚未实现的功能说成已经实现。

## 1. 用户目标与回答风格

- 用户正在准备 AlphaStock / TechLens 项目的面试和答辩。
- 重点主题：Multi-agent、Agent 架构、RAG、PostgreSQL/pgvector、短期/长期记忆、Skill、MCP、子 Agent、上下文压缩、量化模型、推理加速、Hermes 自进化机制。
- 用户希望用简单、能直接面试回答的中文解释；不要堆术语，也不要夸大项目没有实现的功能。
- 回答时最好区分：
  1. “项目当前真实实现”；
  2. “这样设计的原因”；
  3. “如果继续完善，可以怎么做”。

## 2. AlphaStock 项目位置

- 项目目录：`D:\code\ProjectExample\Alpha_stock_new`
- TechLens 项目目录：`D:\code\ProjectExample\TechLens-1.5B`

## 3. AlphaStock 的整体架构

默认生产路径是受约束的 Agent Loop + Python 固定工作流；LangGraph 是兼容适配层，不是默认执行路径。

大致流程：

```text
用户请求
  ↓
API / Gateway / 权限检查
  ↓
主 Agent Planner
  ↓
选择 Skill 或注册表中的 Subagent
  ↓
行情、财务、新闻、文档、记忆工具
  ↓
结构化证据快照 + 上下文压缩
  ↓
研究总结 / Trader 草稿
  ↓
Validation + Output Gate
  ↓
人工审核后才允许发布
```

主 Agent 不让模型直接执行任意 Python 函数，也不允许动态创建任意 Agent。模型只能从服务器给出的固定目录中选择能力。

## 4. 数据源类型

1. 行情和历史价格：Tushare 为主，工具层有 AkShare/yfinance 等备用路径。
2. 财务指标：AkShare/同花顺等接口的财务数据。
3. 新闻：AkShare/东方财富实时新闻，以及 PostgreSQL 中的新闻向量索引。
4. 用户文档：PDF、Word、TXT、CSV；PDF 优先 MinerU，失败时使用 PyMuPDF、pdfplumber、pytesseract OCR 等回退。
5. 图片：VLM 负责提取图片中的事实；图片提取出的文本再切块并生成 embedding。
6. 已审批的操作知识：`agent_runtime/memory/knowledge/` 下的 Markdown。
7. 会话与偏好：PostgreSQL 的 transcript、session summary、user preferences；它们是运行上下文，不是当前市场证据。
8. 派生数据：确定性计算的 KDJ/MA20、分析报告、context snapshot。

## 5. Embedding、向量与 pgvector

### 5.1 Embedding 是什么

是的，生成 embedding 就是把文本转换成向量。例如：

```text
“贵州茅台营收增长”
      ↓ text2vec-base-chinese
[0.12, -0.03, ..., 0.44]  # 768 维浮点向量
```

项目使用 `shibing624/text2vec-base-chinese`，向量维度为 768，并做归一化，查询时用余弦相似度。

### 5.2 PostgreSQL 与 pgvector 的区别

- PostgreSQL：存原文、股票代码、日期、页码、版本、权限、会话等结构化数据。
- pgvector：PostgreSQL 的扩展，提供 `vector(768)` 类型和向量相似度检索能力。
- 如果没有 pgvector，仍然可以存文本和普通字段，但不能直接高效做语义向量检索，只能用 BM25、LIKE 或外部向量数据库。

### 5.3 项目中主要向量表

- `news_vectors`：新闻原文 + 股票代码 + 日期 + 768 维向量。
- `uploaded_document_chunks`：用户文档切块 + session_id + 文件名 + 页码 + 章节 + 版本 + 向量。
- `agent_memory_chunks`：人工审批后的操作知识 Markdown 切块 + 向量。
- `strategy_vectors`：策略类向量数据。

短期会话摘要 `agent_session_memory.memory` 是 JSONB，不是 pgvector；不能把所有东西都向量化。结构化状态适合精确读取，知识文本才适合语义检索。

### 5.4 新闻 RAG

```text
新闻入库 → 文本 embedding → news_vectors
用户问题 → 向量检索
             + BM25 关键词检索
             → RRF 融合
             → 返回新闻证据
```

新闻索引包含股票代码和日期过滤，旧新闻会清理。RAG 评测：Hybrid + RRF 的 Recall@10 约 0.8707；Faithfulness hybrid 约 0.952，但 Context Recall 约 0.567，说明忠实度高但仍有相关证据没召回。

### 5.5 文档 RAG

```text
文件上传
  ↓
MinerU 结构化解析（失败则 PyMuPDF/pdfplumber/OCR）
  ↓
保留标题、段落、表格、页码、版本
  ↓
约 400 字符切块，重叠约 60 字符
  ↓
生成 768 维 embedding
  ↓
写入 uploaded_document_chunks
  ↓
按 session_id + 向量相似度检索
  ↓
补充前后相邻 chunk，返回证据 ID/文件/章节/页码/版本
```

图片本身通常不直接向量化，而是 VLM/OCR 提取文字后再向量化。

## 6. OCR、VLM、页码证据

- OCR = Optical Character Recognition，光学字符识别，把扫描图片中的文字转换成机器可读文本。
- MinerU 主要负责文档结构化解析：标题层级、段落、表格、页码等。
- VLM 主要负责图片内容和图片事实抽取，例如图表数字、截图中的指标。
- OCR 更偏“把字读出来”；VLM 更偏“理解图片并抽取事实”。
- 页码证据不是 OCR。页码证据是 chunk 关联的来源定位信息：文件名、章节、页码、文档版本、`evidence_id`。它让回答能回到原 PDF 的具体位置核验。

## 7. 记忆、Session 和 Session ID

- Session = 一次连续会话的容器。
- `session_id` = 这次会话的唯一 ID，用来把多轮消息、摘要、上传文档和权限绑定在一起。
- 同一个 `session_id`：下一轮可以恢复上下文；换一个 ID 就是新会话。

### 7.1 短期记忆

1. 当前进程内：`ShortTermMemory.messages`，Python 内存列表，服务重启会丢失。
2. 持久化短期记忆：
   - `agent_session_transcript`：原始 user/assistant 对话，审计用。
   - `agent_session_memory`：每个 session 一行的 JSONB 结构化摘要，反复更新，不是无限追加。
   - `user_preferences`：用户偏好。

### 7.2 长期记忆

- `trading_decisions`：历史交易/分析决策摘要。
- `analysis_reflections`：分析反思。
- `backtest_results`：回测结果。
- `agent_memory/knowledge/*.md`：人工审批后的可复用操作知识，再索引到 `agent_memory_chunks` 做 memory-search。

### 7.3 下一轮如何恢复

不是“读取全部历史对话”。流程是：

```text
相同 session_id
  ↓
先读 agent_session_memory：快速知道上次做了什么
  ↓
再读 agent_session_transcript 最近 8 条，且限制约 2400 字符
  ↓
拼入当前上下文窗口
```

这样做是为了兼顾连续性和上下文长度：摘要提供长期状态，最近几条原文保留细节，全部历史会导致 token 爆炸并引入旧信息污染。

注意：代码设计意图是写入 PostgreSQL；`db.get_conn()` 的文档要求调用方显式 `commit()`，而 `remember_run()` 中需要实际验证提交是否完整，面试时不要保证数据库持久化绝对无问题。

### 7.4 未审批模型草稿

模型从对话中总结出的“候选经验”不能直接成为长期知识。例如“MinerU 表格失败时可以尝试 OCR”先进入 `agent_memory_candidates`，状态为 pending；人工 approve 后才生成 Markdown、切块、embedding，最后进入 `agent_memory_chunks`。这是为了防止模型把一次性错误、临时股价或错误建议永久写进系统。

## 8. 上下文窗口和压缩

当前窗口大致由以下部分拼装：

```text
Bootstrap 文件（AGENT.md / IDENTITY.md / TOOLS.md）
+ 已选 Skill 摘要
+ 用户偏好
+ session 摘要
+ 最近 transcript
+ RAG/工具返回的证据
+ 当前用户请求
```

项目设置约 6000 token 的预算：软阈值约 4200，硬阈值约 5100；优先保留高优先级结构化证据，丢弃低优先级可选内容。

工具结果不会全部塞回下一轮：完整 payload 临时保存到 `runtime/tool_results`，上下文只保留工具名、是否成功、来源类型、引用、freshness 和有限预览。上下文过长时先压缩，必要时重试一次。

## 9. Agent、Skill、Tool、Subagent

### 9.1 主 Agent

主 Agent 是 `investment_harness.py` 中的父级 Planner/Orchestrator，负责：

- 接收用户需求；
- 选择 Skill 或 Subagent；
- 控制循环次数（约 4 步）；
- 合并子结果；
- 触发验证、总结和 Output Gate。

### 9.2 Skill 和 Tool

主 Agent 选择的是业务能力（Skill），研究 Harness 选择的是底层工具。两者都不是让模型自由执行代码。

允许的主要 Skill：

- `analysis`
- `document-rag`
- `backtest`
- `memory-search`

主要研究工具：

- `market-price`
- `financial-indicators`
- `stock-news`
- `document-rag`
- `memory-search`

### 9.3 Subagent

当前注册了四类固定 Subagent：

- `technical-researcher`
- `fundamental-researcher`
- `sentiment-researcher`
- `evidence-reviewer`

当前不能让模型运行时随意创造新 Agent。每个子 Agent 有自己的工具、权限、最大轮数和输出字段；一次最多并行 3 个，通常一次运行 2–3 个，整个父流程最多使用这四类角色。没有子 Agent 之间的直接通信，数据通过主 Agent 的 `state`、`updates`、`observations` 传递。

### 9.4 模型选择

不是所有 Agent 都用同一个模型：

- Planner：Qwen planner 或 DeepSeek Reasoner 备用。
- Technical：优先本地 TechLens，失败后 DeepSeek。
- Fundamental：DeepSeek 深度模型。
- Sentiment：DeepSeek 快速模型。
- Trader/最终总结：DeepSeek 深度模型。

## 10. 工具白名单为什么有很多层

这些白名单不是重复配置，而是分别保护不同环节：

1. Skill 白名单：限制主 Agent 能选择哪些业务能力。
2. Tool 白名单：限制每个研究 Harness 能调用哪些底层工具。
3. Permission 白名单：限制当前用户/运行是否拥有 `market:read`、`document:read` 等权限。
4. Subagent 白名单：限制只能调用注册过的角色，防止动态 Agent 膨胀。
5. 输入约束：股票代码必须六位、查询长度有限、top_k 有上限。
6. 安全黑名单：禁止 shell、bash、交易、发布、修改 `.env` 和 `.git`。
7. Output Gate：检查证据是否足够，投资建议是否需要人工审核。

执行过程是：

```text
模型提出动作
  ↓
名称是否在目录中？
  ↓
权限是否满足？
  ↓
参数是否符合约束？
  ↓
执行前再次鉴权
  ↓
结果经过验证和发布门禁
```

这样即使模型被提示词注入，或者输出了 `delete_database`、`trade` 之类的名字，也只能被拒绝，不能直接执行。

## 11. 子 Agent 冲突如何处理

当前项目的真实情况：有失败检测、证据引用和冲突显式化，但没有完整的自动多数投票器。

- 子 Agent 分别写入 `technical_report`、`fundamental_report`、`sentiment_report`，避免互相覆盖。
- 主 Agent 汇总结构化快照和证据 ID。
- 最终研究提示词要求明确说明证据冲突、过期证据和缺失证据。
- `validation_node` 主要识别分支失败；语义上的“两个 Agent 数字不同”仍需要通过证据和规则判断。

推荐的冲突处理方式：

1. 把断言标准化为“股票、指标、报告期、数值、单位、来源时间、证据 ID”。
2. 比较报告期、单位、时间新旧和来源权威性。
3. 官方财报/带页码文档优先于新闻和模型推断。
4. 无法确认时保留冲突，不让模型猜数字。
5. 降低置信度，标记 `partial evidence` 或 `conflict`，必要时阻止发布并人工审核。

技术面偏强、基本面偏弱通常不是事实冲突，而是不同分析维度；可以形成“短期偏强、基本面有风险”的条件性结论。

## 12. 当前项目的降级策略

| 故障 | 当前降级 | 结果 |
|---|---|---|
| TechLens 不可用/超时 | DeepSeek 技术分析 | 保持服务可用 |
| 主 LLM 失败 | 备用模型 | 记录 `used_backup` |
| MinerU 失败 | PyMuPDF/pdfplumber/OCR | 尽量保留文档和页码 |
| 某个分析分支失败 | 最多 replan 一次，重跑失败分支 | 标记部分证据 |
| Planner 输出非法或没选 analysis | 固定 analysis skill fallback | 避免空报告 |
| 所有分析分支失败 | Output Gate 阻止发布 | 证据不足 |
| RAG 没有召回 | 输出“证据不足” | 不补造事实 |
| 上下文过长 | 压缩工具观察和历史 | 保留来源与证据 ID |
| 结论缺少证据 | 人工审核/阻止发布 | 不直接发布投资建议 |

原则：降级可以降低结果完整度，但不能用虚构数据伪装成功。

## 13. API 响应时间长的排查

总耗时可以拆成：

```text
T_total = 鉴权/排队
        + Planner LLM
        + 行情/财务/新闻工具
        + Subagent
        + RAG/数据库
        + 最终总结/Trader LLM
        + 持久化
```

项目已有的观测点：

- `config/llm_config.py` 记录 LLM `latency_ms`；
- `research_harness.py` 记录每个工具耗时；
- `subagents.py` 记录子 Agent 耗时；
- `agent_trace` 和 `run_id` 可以串起一次请求。

排查顺序：

1. 先请求 `/health`，判断是否服务整体异常。
2. 读取 `run_id` 的 trace，找出最慢阶段。
3. 只跑 technical，再跑 fundamental/sentiment，定位是哪条分支慢。
4. 单测外部行情/新闻接口，排查网络、限流和接口超时。
5. 单测 PostgreSQL/pgvector，检查连接池和慢 SQL。
6. 比较冷启动/热启动，排查模型或 embedding 模型反复加载。
7. 检查是否发生备用模型调用、重试或 replan；它们会叠加耗时。
8. 用 P50/P95 统计尾延迟，不要只看一次请求。

常见原因：外部数据源慢、模型网络/排队慢、DeepSeek 备用调用、本地 TechLens 超时、文档 MinerU/OCR/VLM 慢、上下文太长、数据库连接或查询慢。

可采取的优化：给每个外部调用设置 timeout；复用模型；行情/基本面/情绪并行；Planner 使用快模型；限制最大工具调用和 Agent 步数；缓存行情、解析结果和 embedding；超时返回部分证据或“证据不足”，不要无限等待。

## 14. Faithfulness 和 RAG 评测

- Faithfulness 主要指生成答案是否忠实于检索到的上下文，低通常表现为幻觉、改数字、把证据没有说的内容补出来。
- 但 Faithfulness 低不一定只怪生成模型：解析错误、OCR 数字错误、召回了错误 chunk、日期过滤错误，都会污染上下文，最终导致答案不忠实。
- Context Recall 低：相关证据没有召回。
- Context Precision 低：召回结果中噪声多。
- 所以排查 Faithfulness 要分解析、检索、生成三层检查。

## 15. TechLens 实际量化与部署

TechLens-1.5B 是 Qwen3-1.7B，通过 SFT + DPO 训练。

- 训练：BF16 + LoRA/QLoRA 思路；SFT 和 DPO 配置在 `TechLens-1.5B/configs/training/llamafactory/`。
- 已有实际 INT8 与 BF16 对比；当前 GPU 部署保留 BF16，因为 INT8 在该环境不一定更快。
- 当前没有证据表明已经完成 AWQ/W4A16、GPTQ、vLLM、SGLang、FlashAttention、Prefix Cache 或连续批处理部署。
- API 使用 Transformers + PEFT + FastAPI，TechLens 在 8088 端口，本地失败后调用 DeepSeek。

## 16. Hermes 机制与本项目的区别

Hermes 的四层：

1. Python 计数器决定何时触发反思；
2. 后台 Agent 异步执行反思；
3. 固定 Prompt 判断要保存什么；
4. memory/skill 工具写入文件，下一次启动由 prompt builder 重新加载。

核心分工：

```text
什么时候反思：代码
反思什么：模型
怎么持久化：工具
是否安全：扫描、权限和人工审核
```

AlphaStock 没有完全照搬 Hermes 的自动写文件机制，而是使用 session memory、transcript、候选记忆、人工 approve、Markdown 索引的更保守流程。

## 17. 面试时必须避免的过度表述

- 不要说项目已经用 vLLM/SGLang，实际 API 是 Transformers。
- 不要说已经实现 AWQ/GPTQ，当前主要有 INT8/BF16 对比。
- 不要说子 Agent 可以动态创建，实际是固定注册表。
- 不要说系统有完整自动冲突投票，实际是证据追踪、失败检测、冲突显式化和 Output Gate。
- 不要说所有会话上下文都存在 Python 内存；持久化版本在 PostgreSQL。
- 不要说所有记忆都存在 pgvector；JSONB/关系表保存结构化状态，只有可检索知识切块后进入向量表。
- 不要把 operational memory 当成当前股票事实；当前事实必须来自最新行情、新闻或文档证据。

## 18. 推荐的总括面试回答

> 我的系统采用受约束的中心化 Agent 架构。主 Agent 负责规划和汇总，子 Agent 只从固定注册表中选择，每个子 Agent 都有独立权限、工具和输出契约。数据层把结构化业务状态放在 PostgreSQL，把文本 chunk 的语义向量放在 pgvector，并结合关键词检索和向量检索。所有分析结果都必须带来源、时间和证据 ID；出现冲突时优先回到原始证据，无法确认就降低置信度或阻止发布。系统通过模型、数据源、解析器、上下文和输出门禁多层降级，保证失败时宁可返回部分证据，也不生成未经证实的投资结论。
