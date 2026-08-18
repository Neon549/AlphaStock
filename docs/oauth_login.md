# 微信和 QQ 登录配置

AlphaStock 已接入微信网站 OAuth 和 QQ 互联 OAuth。个人项目可以完成开发和本地测试，但能否正式上线取决于微信开放平台、QQ 互联对应用类型、回调域名和审核资质的要求。

## 后端配置

把下面配置写入服务器上的 `.env`，不要写进 React 代码，也不要提交到 Git：

```dotenv
FRONTEND_URL=https://你的前端域名
OAUTH_COOKIE_SECURE=true

WECHAT_APP_ID=你的微信网站应用AppID
WECHAT_APP_SECRET=你的微信网站应用AppSecret
WECHAT_REDIRECT_URI=https://你的后端域名/api/v1/auth/wechat/callback

QQ_APP_ID=你的QQ应用AppID
QQ_APP_KEY=你的QQ应用AppKey
QQ_REDIRECT_URI=https://你的后端域名/api/v1/auth/qq/callback
```

本地 HTTP 开发可以使用 `FRONTEND_URL=http://localhost:5173` 和 `OAUTH_COOKIE_SECURE=false`。真实授权回调通常需要公网 HTTPS 测试域名或内网穿透地址，并且平台后台登记的地址必须完全一致。

## 平台回调地址

- 微信网站应用：`https://你的后端域名/api/v1/auth/wechat/callback`
- QQ 网站应用：`https://你的后端域名/api/v1/auth/qq/callback`

前端按钮会跳转到 `GET /api/v1/auth/wechat` 或 `GET /api/v1/auth/qq`。后端校验一次性 `state` Cookie，再用 `code` 换取平台身份，最后复用 AlphaStock 的 opaque token。

## 数据库行为

服务启动时会幂等创建 `oauth_accounts` 表。已有 `users`、密码登录、Google 登录和会话 token 不会被删除或重建。微信和 QQ 通常不提供可验证邮箱，因此不会根据昵称自动合并账号；首次登录会创建稳定的本地账号标识。

没有 AppID、AppSecret 或 AppKey 时，按钮已经接好但后端会安全返回 503；这不会影响账号密码和 Google 登录。
