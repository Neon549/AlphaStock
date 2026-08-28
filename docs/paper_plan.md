# AlphaStock Agent 论文规划 v0.1

## 1. 当前结论

AlphaStock 当前最适合发展的论文方向，不是单独声称“BGE 重排提升了 RAG”，而是研究一个更完整的问题：

> 在金融研究 Agent 中，证据约束、引用回链、拒答策略和输出治理，能否提升端到端答案的可验证性与安全性，并且通过固定快照评测和运行轨迹持续发现回归？

暂定英文题目：

**Evidence-First Evaluation and Reliability Control for Financial Research Agents**

暂定中文题目：

**面向金融研究 Agent 的证据优先评测与可靠性治理方法**

这条主线比“做了一个投研 Agent”更像论文，也比“接入 BGE 重排”更有研究空间。

## 2. 项目已有基础

当前项目已经具备论文实验所需的大部分工程骨架：

- 统一 Agent Harness：运行生命周期、断点、恢复、重试、追踪和权限边界；
- 多来源金融 RAG：BM25、Dense、RRF、BGE Cross-Encoder、实体和时间过滤；
- 证据层：市场数据、新闻、公告、文档、回测结果及引用回链；
- Output Gate：证据不足、过期证据、高风险表述和发布审核；
- E2E 评测：答案、工具、参数、引用、拒答、恢复和安全 Rubric；
- 固定快照、数据 manifest、消融协议、SLO、红队和 release quality gate。

已有结果可以作为“现状和动机”，但要注意边界：

- FinanceBench 外部 Gold：150 条，answer accuracy 43.24%，citation-grounded accuracy 30.41%；
- 内部候选集和固定新闻集目前只能作为工程诊断，不能写成 production accuracy；
- BGE 的现有 A/B 结果是指标权衡，不支持“全面提升”的结论；
- 当前仓库仍缺少经过双 reviewer 复核、真正 untouched 的最终测试集。

## 3. 论文核心假设

### H1：证据优先约束能提升 grounded correctness

实体过滤、时间过滤、来源优先级、引用检查和证据不足时拒答，应该比无约束 RAG 更少产生无依据结论。

### H2：可靠性不能只看 Faithfulness

金融 Agent 应同时评估：检索命中、答案事实正确、引用页码正确、拒答合规、工具轨迹、安全和重复运行稳定性。

### H3：固定快照和轨迹评测能发现传统答案指标看不到的回归

系统可能保持答案流畅度，却出现证据漂移、工具误调用、过期数据使用、重复调用或恢复失败。因此需要把检索、答案和运行轨迹放进同一套评测协议。

## 4. 计划贡献

以下贡献只有在独立数据和消融实验完成后才能写进论文：

1. **可靠性评测协议**：把金融 Agent 的检索、答案、引用、拒答、工具轨迹、恢复和安全放入统一指标体系。
2. **证据优先运行管线**：通过实体/时间过滤、来源分层、证据引用和 Output Gate 限制无依据输出。
3. **系统化消融结果**：比较无治理、仅 RAG、RAG+证据校验、完整治理管线在质量、稳定性、延迟和成本上的差异。
4. **失败分类与分析**：区分证据发现失败、跨页组合失败、引用映射失败、模型推理失败、工具轨迹失败和安全失败。

## 5. 必须比较的实验变体

| 变体 | RAG | 证据校验 | 引用/拒答 | Output Gate | 目的 |
|---|---|---|---|---|---|
| V0 | 基础检索 | 否 | 否 | 否 | 最小 baseline |
| V1 | 受限检索 | 是 | 否 | 否 | 测量证据过滤的作用 |
| V2 | 受限检索 | 是 | 是 | 否 | 测量引用和拒答的作用 |
| V3 | 完整管线 | 是 | 是 | 是 | AlphaStock 主方法 |
| V4 | 完整管线 | 是 | 是 | 是，关闭某一治理模块 | 消融实验 |

必要时再增加：

- BGE on/off；
- Query rewrite on/off；
- 新闻/公告来源优先级 on/off；
- Single Agent 与受控 reviewer 的对比；
- 恢复机制 on/off；
- 同一任务重复 4 次，报告 Pass@4 和 Pass^4。

不要把所有模块都作为“创新点”。论文只需要一个主方法，其余模块作为受控变量和消融项。

## 6. 评测指标

### 检索层

- Recall@K、MRR、nDCG；
- citation hit rate；
- citation backlink/page correctness；
- entity/time filter violation rate。

### 答案层

- answer accuracy；
- grounded answer accuracy；
- unsupported-answer rate；
- abstention compliance；
- Faithfulness、Answer Relevancy 作为辅助指标，不替代事实正确率。

### Agent 轨迹层

- final task success rate；
- tool selection/parameter/order accuracy；
- tool failure and retry recovery rate；
- redundant call rate；
- average/max steps；
- Pass@4 与 Pass^4。

### 工程和安全层

- P50/P95/P99 latency；
- token 和 monetary cost；
- stale-evidence rate；
- prompt-injection、越权工具、PII 泄露和高风险表述失败数。

## 7. 数据集计划

### 第一层：公开外部 Gold

保留 FinanceBench 作为公开可复现基准，明确写成 external Gold，不声称它代表 AlphaStock 线上流量。

### 第二层：金融 Agent 任务集

目标建立 80–120 条经过审核的任务，覆盖：

- 单公司事实；
- 财报跨页、多来源和跨期问题；
- 新闻时效性验证；
- 多股票比较；
- 信息不足时拒答；
- 多轮上下文和复合任务；
- 高风险发布/交易权限边界；
- 工具失败、重试和恢复。

每条任务至少记录：答案事实、Evidence ID、页码、时点、允许拒答条件、理想工具轨迹和失败标签。

### 数据质量要求

- 两名 reviewer 独立标注；
- 冲突进入仲裁；
- train/validation/test 按公司和时间隔离；
- 最终 test 必须 untouched；
- synthetic 和 candidate 数据不能伪装成 production Gold。

## 8. 论文工作节奏

### 第 1 周：冻结论文问题

- 固定题目、研究假设和主方法；
- 画方法流程图；
- 盘点现有数据和可复现实验；
- 建立结果表模板。

### 第 2 周：冻结数据与 baseline

- 固定 FinanceBench 运行方式；
- 完成第一批人工审核任务；
- 冻结 corpus、prompt、模型和工具快照；
- 跑 V0 baseline。

### 第 3–4 周：完成主实验

- 跑 V1、V2、V3；
- 完成 BGE、Query rewrite、来源优先级和 Output Gate 消融；
- 每个主要配置重复运行，记录延迟、token 和成本。

### 第 5 周：可靠性和失败分析

- 运行多次重复实验；
- 完成红队和权限边界测试；
- 统计失败 taxonomy；
- 对关键结果进行人工复核。

### 第 6 周：论文初稿

论文结构固定为：Introduction、Related Work、Problem、Method、Evaluation Protocol、Results、Failure Analysis、Limitations、Conclusion。

### 第 7–8 周：投稿判断

- 检查是否有独立测试集；
- 检查主方法是否真的优于 baseline；
- 检查所有结论是否有统计和人工复核支持；
- 决定 CCF-A 冲刺，或先投更匹配的会议/Workshop 并准备预印本。

## 9. CCF-A 冲刺门槛

只有同时满足以下条件，才把 CCF-A 作为主要投稿目标：

- 有清晰且非纯工程集成的主创新；
- 有独立、未调参的最终 test；
- 有强 baseline 和完整消融；
- 有至少一项端到端可靠性提升，而不是只提升一个 RAGAS 指标；
- 有人工审核和失败分析；
- 代码、数据 schema、配置和运行方式可复现。

如果数据、创新或独立测试集不足，仍然可以保留 CCF-A 为长期目标，但不应该为了等级硬投一篇证据不足的论文。

## 10. 下一步唯一任务

先完成 **V0 baseline + 数据清单 + 结果表模板**，暂时不继续堆新功能。

第一轮要回答三个问题：

1. 现有 FinanceBench 和内部 candidate 数据中，哪些可以合法、清晰地用于论文？
2. V0 无治理 baseline 的端到端结果是多少？
3. V3 完整证据治理相对于 V0，究竟改善了哪些指标、牺牲了哪些指标？

回答完这三个问题，论文题目和创新点再最终定稿。
