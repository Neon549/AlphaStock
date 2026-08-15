# AlphaStock 当前项目进度摘要（2026-08-15）

## 一句话定位

AlphaStock 已经从“金融问答 Demo”发展为一个带有受控 Agent Runtime、权限治理、工具调用、RAG、引用回链、复合意图、动态临时 reviewer、E2E 评测和发布质量门禁的金融 Agent 工程项目。

当前最重要的边界是：工程评测基础设施已较完整，但真正的脱敏生产 Gold 数据尚未进入仓库，因此还不能声称线上准确率或 production-tier 质量。

## 当前已经完成的主要能力

### 1. Agent Runtime 与治理

- 有 runtime、权限控制、工具白名单、预算、审计 trace 和发布门禁。
- 高风险交易、发布、越权等动作需要确认或人工审核。
- 工具失败、重试、恢复和证据门禁已有结构化记录。
- 支持受控动态 ephemeral subagent：只允许审核类模板，一次性运行、无工具权限、记录创建/结果/销毁生命周期。

### 2. 意图识别与复合意图

- 保留规则优先、fastText 辅助、LLM fallback 的路由链路。
- 已覆盖简称、错别字、多股票、缺失槽位、回测时间窗口、高风险交易和复合任务候选集。
- 简单复合请求走确定性 DAG；复杂多实体、多跳请求支持受约束 JSON decomposition。
- Query rewrite 保留 `original_query`、`rewritten_query`、`rewrite_reason` 和过滤字段，实体/年份/金额不允许被模型覆盖。
- 当前 smoke/candidate 的 1.0 结果只证明固定样例无回归，不能代表线上意图准确率。

### 3. RAG 与 Rerank

- 有 BM25、dense、RRF、BGE Cross-Encoder rerank 和实体/时间范围过滤。
- BGE 主线路已完成候选评测和安全边界收紧；新闻实体必须通过本地股票字典或代码验证。
- 已检查 Top-20 → Top-10 → Top-5 候选漏斗，但当前固定集合没有证明通用提升。
- Query rewrite 已做候选 A/B，结果只能称为 candidate non-regression diagnostic，不能称生产提升。

已记录的可引用结果：

| 评测层 | 结果 | 边界 |
|---|---:|---|
| FinanceBench 外部 Gold | 43.24% answer accuracy | 外部公开基准，不是线上流量 |
| FinanceBench 引用 grounded | 30.41% | 需要正确答案和正确页码引用 |
| FinanceBench metadata-free retrieval | Recall@10 13.67% | 全文档发现能力仍弱 |
| 内部新闻固定集 | scoped BM25 Top-5 关键字覆盖 0.4836 | 诊断集，不是生产指标 |

当前 RAG 的核心问题仍是证据发现、跨页组合、页码映射和引用完整性，不是单纯 Faithfulness 不足。

### 4. Google 登录修复与部署

- 后端 OAuth state、Secure/HttpOnly/SameSite cookie、callback 校验和 next-page 白名单已完成。
- React 前端不再把浏览器提交的 email/name/id 当作身份凭据，而是直接提交 Google `access_token` 给后端校验。
- 后端部署 commit：`371b421`。
- 前端部署 commit：`69c290e`。
- GitHub Actions 后端与前端部署均已成功。
- 在线验证：站点加载新 React bundle，Google OAuth endpoint 返回 Google redirect 并设置安全 state cookie。

## 当前新增的评测基础设施

### P0：发布质量门禁

文件：`evaluation/release_quality_gate.py`

统一检查：

- 代码回归；
- 治理回归；
- RAG / E2E / 引用指标相对 baseline 不下降；
- P95 延迟；
- 平均 Token 与成本；
- 红队样本存在且高风险失败为 0。

缺失项默认阻断。它不自动生成数据，也不把 candidate 当 production。

### P0：中文真实 Gold intake

文件：`evaluation/production_gold_intake.py`

校验：

- `deidentified_session` / `production_bad_case` 来源；
- SHA-256 source fingerprint 与 corpus hash；
- train / validation / test split；
- 事实、财报、新闻、多股票、高风险、缺失信息、多轮和复合任务分类；
- Evidence ID、页码、答案事实、是否允许拒答；
- 双 reviewer 与 review 时间；
- PII 与原始会话字段禁止进入仓库。

### P1：E2E 稳定性与轨迹

文件：`evaluation/financial_agent_e2e.py`

已输出：

- `pass_at_4`；
- `pass_caret_4`（四次均成功）；
- `final_task_success_rate`；
- 平均/最大步数；
- 工具调用成功率；
- 重复工具调用率；
- 可选 `trajectory`：工具选择、参数、顺序、冗余调用、禁止工具、澄清和拒答。

### P1：运行 SLO

文件：`evaluation/operational_slo.py`

对受控 telemetry 聚合：

- 并发；
- P50/P95/P99；
- Provider/工具失败率；
- 重试尝试率与恢复率；
- fallback 使用率；
- input/output/total Token；
- 平均成本。

缺失 telemetry 不会被默认为 0 失败。

### P1：安全红队

文件：`evaluation/red_team_eval.py`

覆盖：

- 直接/间接 Prompt Injection；
- 越权工具；
- 绕过确认；
- PII 外泄；
- 收益保证话术；
- 过期数据。

输出可直接接入质量门禁的 `total_cases` 与 `high_risk_failures`。

## 测试状态

当前全量测试：

```text
319 passed, 5 warnings
```

警告主要来自：

- jieba/pkg_resources deprecation；
- Starlette/httpx 兼容提示；
- Pydantic forward reference warning。

它们不是当前功能失败，但应列入后续依赖升级任务。

## 当前还不能声称的内容

不能声称：

- 线上意图识别准确率 100%；
- BGE 或 Query Rewrite 已经普遍提升生产 Recall/Precision；
- 多 Agent 已被证明优于 Single Agent；
- 当前候选集就是 production Gold；
- 已有完整线上 P95/P99、成本、provider failure 和灰度回滚闭环；
- 红队覆盖了所有未知攻击。

## 下一步优先级

### P0：进入真实数据准入

1. 从受控环境导出脱敏 `deidentified_session` / `production_bad_case`。
2. 送入 `production_gold_intake`。
3. 两名 reviewer 独立标注；冲突进入仲裁。
4. 建立 80–120 条 FinancialAgent E2E v1，覆盖高风险和失败恢复。
5. 每题四次真实运行。
6. 冻结 untouched test，写入 manifest 的 production tier。

### P0：接通实际发布流程

真实报告齐全后，将：

- workflow regression；
- governance regression；
- RAG/E2E/citation 报告；
- operational SLO；
- red-team 报告；

组合成 `release_quality_gate` 输入，并把退出码接到部署前门禁。

### P1/P2：真实数据之后

- 多轮用户模拟与 Full Team vs Single Agent 消融；
- DeepSeek vs Qwen、RAG on/off、rerank on/off、Memory on/off；
- 双 judge、顺序交换、专家抽样复核；
- 并发阶梯压测、provider failure、自动回滚和线上 bad case 回流。

## 新 chat 建议读取入口

先读本摘要，再读：

1. `openspec/release-quality-gate-v1/spec.md`
2. `evaluation/financial_agent_e2e.py`
3. `evaluation/financial_agent_e2e_production_admission.py`
4. `evaluation/production_gold_intake.py`
5. `evaluation/operational_slo.py`
6. `evaluation/red_team_eval.py`
7. `project-log/2026-08-15-p0-quality-gate-and-gold-intake.md`

继续工作时，默认从“受控脱敏真实导出进入 intake”开始，不要创建 synthetic 数据来填充 production tier。
