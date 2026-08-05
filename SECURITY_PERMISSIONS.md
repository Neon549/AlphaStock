# AlphaStock 安全权限笔记

## 一、当前项目的安全能力

项目本身已经有一些安全基础：

- `Gateway -> InvestmentRuntime -> Workflow` 统一了 Web、CLI、Webhook 的 Agent 运行入口，并有 `event_id` 幂等去重和 PostgreSQL 审计记录。
- `ResearchHarness` 只允许注册表中的只读研究工具，限制最多 3 次工具调用、结果长度和上下文长度，并保留 trace。
- Skill Registry 会校验 skill manifest、版本指纹和声明权限，模型不能凭空选择未注册的 skill。
- 投资建议有确定性的输出治理：缺失证据、工具失败和“保证收益”等表述会被阻断；即使生成成功，也必须经过人工 review 才能发布。
- 上传文档按 `session_id` 做 RAG 隔离，研究 Agent 默认只能使用市场、文档和已审核 memory 权限。
- Webhook 已使用 HMAC 签名和事件幂等键。

## 二、本次补齐的问题

原来的主要风险是：token 永不过期并且可明文落库；密码使用快速 SHA-256；聊天、分析和计算接口可以匿名调用；对话接口信任 URL/body 中的 username；上传 session 可以被猜测后清理或读取；Google 登录信任浏览器提交的 email/name/google_id；CORS 允许任意来源；Agent 权限没有统一的工具级安全管道。

本次改动：

- 新密码使用 `scrypt`，旧 SHA-256 账号在成功登录后自动迁移。
- 新登录 token 使用高熵 opaque token，数据库只保存 SHA-256 digest，默认 24 小时过期；旧 token 只保留兼容验证路径。
- 登录增加同用户名时间窗口限流，并统一错误文案，降低账号枚举和暴力尝试风险。
- 聊天、分析、回测、扫描、Alpha 打分、上传和删除临时资料都要求认证；优先读取 `X-Auth-Token` 或 `Authorization: Bearer`，兼容旧 body token。
- 对话读写/删除校验 token 对应用户，不能用 URL 中的 username 越权访问别人的数据。
- 新增 `upload_sessions` 所有权表；文档 session 首次使用时绑定用户，之后只能由同一用户访问或删除。
- Google 登录改为后端向 Google `userinfo`/`tokeninfo` 校验 access token 或 ID token，不再相信前端解析出来的身份字段。
- CORS 改为显式来源列表，默认只允许正式站点和本地开发端口；增加 25 MB 请求体上限。

## 三、四层权限管道

实现位置：`control_plane/security.py`。

### 第一层：规则过滤

规则位于 `config/security_permissions.json`，也可以通过 `ALPHASTOCK_PERMISSION_POLICY` 指定外部 JSON。规则格式是 `tool(target)` 或精确 capability 名称。

- `deny` 优先级最高，命中后直接拒绝。
- `allow` 只表示用户明确允许的能力。
- 未命中规则不会自动放行，而是进入后续层。

默认禁止 shell、`.git`、`.claude`、`.env` 和高影响的 publish/trade 操作；允许只读工具、市场读取、文档读取、memory 读取和项目内分析计算。

### 第二层：工具自检

工具根据本次操作内容进行确定性检查：

- 文件写入必须位于项目根目录内，禁止路径穿越。
- `.git`、`.claude`、`.env`、credentials、secret、password、token 等敏感路径是 bypass-immune，不能被 bypass 覆盖。
- shell 会检查破坏性命令、命令注入字符、`curl | sh`、`git push`、`git reset --hard`、编码 PowerShell 等高风险模式；解析失败默认拒绝。
- publish/trade 永远需要更高层的人审边界，不能由 Agent 自行放行。

### 第三层：模式兜底

支持 `default`、`acceptEdits`、`plan`、`bypass`、`dontAsk`、`auto`：

- `default`：未决操作返回 ask，交给上层人工确认。
- `acceptEdits`：仅允许项目目录内普通编辑。
- `plan`：只读，不允许写操作。
- `dontAsk`：未被明确 allow 的操作直接拒绝，适合无人值守。
- `bypass`：只跳过本层确认，不能绕过 deny 规则和工具自检。
- `auto`：进入第四层；当前实现宁可误拦，不让未知操作自动放行。

### 第四层：动态兜底

`auto` 模式先走零副作用工具和项目内编辑的快速路径，再进入更严格的 Stage 2 分类。未知工具、外部路径和 shell 默认拒绝。

当前项目没有把“另一个 LLM”当作最终安全边界，因此动态分类器是确定性的 fail-closed 实现。未来如果接入独立 AI 分类器，也只能作为这一层的辅助判断，不能覆盖硬 deny、路径保护和人工 review。

## 四、Agent 工具权限

`ResearchHarness` 的工具权限如下：

| 工具 | 权限 | 副作用 |
|---|---|---|
| `document-rag` | `document:read` | 只读当前用户 session 文档 |
| `memory-search` | `memory:read` | 只读已审核 memory |
| `market-price` | `market:read` | 只读市场数据 |
| `financial-indicators` | `market:read` | 只读财务数据 |
| `stock-news` | `market:read` | 只读新闻数据 |

模型只能在 Registry 提供的目录中选择工具；缺权限、非法工具、重复调用、超出次数或权限管道拒绝时，运行停止。工具 observation 会被压缩并写入 trace，主模型文本不作为权限证明。

## 五、仍需在生产环境补上的能力

- 将 token/session 限流从进程内字典迁移到 Redis 或网关，支持多实例共享限流状态。
- 生产环境将 `ALPHASTOCK_CORS_ORIGINS` 配置为真实域名，不使用本地开发来源。
- 数据库连接使用最小权限账号，并对备份、日志和监控中的 token、邮箱、上传内容做脱敏。
- 若未来开放 shell、插件或 MCP，必须为每个工具实现 AST/参数级自检和独立沙箱，不能仅增加一条 allow 规则。
- 对 publish/trade 等高影响操作继续保持人工审批，并增加 CSRF、防重放、审计告警和管理员角色模型。
- Google 回调生产环境应进一步校验 `aud`、`iss`、`email_verified` 和配置的 client ID。

## 六、面试版总结

“我的项目采用纵深防御。请求先经过静态 deny/allow 规则，再由工具根据具体路径和参数做确定性自检，然后由 default、plan、dontAsk、bypass、auto 等模式提供风险兜底，auto 再走保守的动态分类。deny 和敏感路径是 bypass-immune，未知和故障默认 fail closed。Agent 只能够调用 Registry 中声明过的只读技能，权限、调用次数、上下文和输出发布都有独立治理；投资建议还必须经过人工 review。这样既防止模型乱调工具，也防止认证、会话隔离和发布链路被绕过。”
