# FinancialAgent-E2E-v1 数据与评测准入协议

## 当前状态

`financial_agent_e2e_candidate_v1.jsonl` 是 12 条合成 candidate fixture，只用于验证
任务、轨迹、Rubric 与汇总器的契约。`financial_agent_e2e_review_queue_v1.jsonl` 已扩展至
96 条公开来源/合成 reviewer queue，覆盖研究、路由、安全和恢复场景。两者都不含真实
用户会话，不能用于模型选型、上线批准、简历或生产成功率声明。

## 生产级扩展目标

在 80–120 条任务达到以下条件前，数据集不得改为 `production` tier：

1. 冻结任务文本、文档版本、工具版本和收集日期；每项记录 SHA-256。
2. 查询来自脱敏会话或独立的 production bad case，不得从训练、调参或候选 fixture
   改写而来。
3. 两名 reviewer 独立标注任务目标、4–8 个原子 Rubric、关键/安全标记、允许证据、
   页码、澄清条件和失败 taxonomy；分歧由第三人仲裁。
4. 每个策略每任务至少运行四次；报告 Avg、Pass@4、Pass^4、分桶结果、延迟、成本、
   工具调用和恢复率。
5. 对 LLM Judge 抽样与人工 rubric 标注比较，报告一致性与分桶偏差；高风险安全
   Rubric 必须保持可确定性审计。

## 首批覆盖

当前 fixture 覆盖：单股事实、多来源研究、跨报告期、上下文指代、缺失信息澄清、
复合任务、高风险交易/发布、工具失败恢复、证据冲突、时间语义和页码引用。

`evaluation.financial_agent_e2e_review` 接收 reviewer overlay；仅当两名不同 reviewer
对 Rubric、允许证据和 failure taxonomy 一致，且原始来源为 `deidentified_session` 或
`production_bad_case` 时，条目才会成为“可进入 production admission”的候选。当前 96 条
会被该工具明确标记为不可提升来源，而不是被误报为已人工复核。
