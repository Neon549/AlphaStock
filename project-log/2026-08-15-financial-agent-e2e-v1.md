# 2026-08-15：FinancialAgent-E2E-v1 启动记录

## 已实施

- 新增中文 OpenSpec，冻结端到端任务、运行轨迹、4–8 个原子 Rubric、四次运行指标和失败 taxonomy 契约。
- 新增离线评分器 `evaluation/financial_agent_e2e.py`：对最终答案、工具/参数、页码引用、澄清、任务图、发布状态、无越权副作用和恢复分别评分；严格成功要求所有 Rubric 通过，高风险任务额外要求 safety Rubric 通过。
- 新增 runtime 结果适配器，可将现有 `agent_trace`、任务计划、证据引用与发布状态导出为 E2E run record；不执行模型、工具或交易。
- 新增 12 条冻结 candidate fixture，覆盖单股事实、多来源研究、跨期、上下文、澄清、复合任务、交易/发布门禁、失败恢复、证据冲突、时间语义和引用。
- 扩展为 96 条 reviewer queue；保留 parent case、公开来源/合成标记、任务/工具/文档快照和 4–8 条 Rubric。
- 新增双 reviewer 与仲裁状态机：一致、拒绝、待仲裁和仲裁结果均可审计；synthetic/public-source 即使一致通过也不可进入 production admission。
- 新增真实来源 intake 边界：只接收受控导出后已脱敏的 `deidentified_session` / `production_bad_case`，要求不可逆来源指纹与固定快照；拒绝用户/会话/trace/IP 字段及常见 PII。仓库未发现合规真实导出，因此未创建伪造 real case。
- 在 manifest 中登记快照与 claim boundary。

## 已验证

E2E 评分、运行稳定性（Avg / Pass@4 / Pass^4）、失败 taxonomy、runtime 适配与 manifest 回归测试均通过。

## 边界和后续

12 条 fixture 只验证评测框架，不能作为真实模型成功率。现已扩展 96 条公开来源/合成 reviewer queue，并加入双 reviewer、仲裁和来源准入检查；它仍不是生产集。下一批应替换为脱敏真实复杂 Query，经双 reviewer + 仲裁后收集四次真实运行，并对 LLM Judge 与人工 Rubric 做校准。

## 生产级准入门禁补充

- 新增 `evaluation/financial_agent_e2e_production_admission.py`，把 intake-produced real case、双 reviewer/仲裁状态和每个 `case × variant` 的四次受控环境运行连接为可执行报告。
- 运行记录必须包含 ISO 执行时间、runtime 快照 SHA-256 和 trace 脱敏版本；重复 run、session/user/IP/raw trace 等身份或原始字段会被拒绝。
- 明确区分 `dataset_admission_ready` 与 `release_gate_passed`：前者允许保留真实失败样本来统计稳定性；后者才要求关键 Rubric 与高风险安全 Rubric 全部通过。
- review 阶段不再只相信 `origin` 标签，额外要求 intake 生成的不可逆来源指纹和当前脱敏版本，防止手工 fixture 冒充真实来源。

## 前端可信表达与可用性优化

- 检查并修改独立前端工作区 `D:\code\ProjectExample\frontend\Neon_stock_trading_frontend`；首屏改为可收缩双栏，修复中等桌面宽度下演示卡可能横向裁切，补充手机断点。
- 删除无法审计的 `100K+` 用户、`978 reviews`、固定回测收益/胜率等数字，演示区改为明确的“示例研究工作流”，避免把样例或回测表现当作普遍承诺。
- 首屏、研究演示、统计和风险区统一强调数据时点、引用来源、人工复核与“不自动交易”边界；占位的法律链接替换为风险说明或明确咨询入口。
- 修复登录后路由：静态页首次交接 token 后，`/app?page=backtest|alpha|scan|filter|chat` 能进入对应 Streamlit 页面，且 Streamlit 创建 session 后会清除 URL 参数。

## Google OAuth 接入修复

- 根因是前端 Google 按钮原先只执行 `alert('Google 登录开发中')`，未曾调用后端；线上 `/api/v1/auth/google` 已配置且可重定向至 Google。
- 前端现调用真实 OAuth 入口，并处理 callback 返回的 `google_login=success`，存入已有会话交接路径后进入 `/app`；`next_page` 使从回测、选股等入口登录的用户回到相应功能页。
- 后端新增 10 分钟、`Secure`、`HttpOnly`、`SameSite=Lax` 的 OAuth state/next-page cookie，callback 用常量时间比较校验 state、限制可跳转页面白名单并清理 cookie。
- 新增 `tests/unit/test_auth_google.py`，覆盖 state、cookie 属性、错误 state 拒绝与目标页白名单。

## 2026-08-15 部署记录

- 后端 OAuth 修复以独立提交 `371b421` 推送至 `Neon549/Alpha_stock:main`；GitHub Actions `Deploy Backend` 已成功完成，包含回归、服务器拉取与 `alphastock-api.service` 重启。
- 前端远端 `main` 已迁移为 React/Vite；未覆盖其新架构，而是将 Google 修复移植到 `AuthModal.jsx` 与 `api.js`：客户端把 Google `access_token` 原样交给后端 `/auth/google/token` 验证，不再把浏览器拿到的 email/name/id 当作身份凭据。
- React 前端提交 `69c290e` 推送至 `Neon549/Alpha_stock_frontend:main`；`Deploy Frontend` 已成功构建、上传静态产物并 reload Nginx。
- 线上只读验证：首页加载新 bundle，其中含 `access_token` 与 `/auth/google/token`；`/api/v1/auth/google` 返回 Google redirect 且下发 `Secure + HttpOnly + SameSite=Lax` state cookie。未代替用户登录 Google 账户。
