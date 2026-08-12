# AlphaStock Remote MCP

## Endpoint and boundary

The production MCP endpoint is mounted into the existing FastAPI process:

```text
https://alphastock.cloud/api/v1/mcp/
```

It uses MCP **Streamable HTTP** and Bearer authentication. It does not expose
PostgreSQL, arbitrary files, publication approval or trade execution.

| Tool | Scope | Boundary |
| --- | --- | --- |
| `list_research_capabilities` | `knowledge:read` | Lists registered public Skills and policy only. |
| `research_stock` | `research:read` | Runs the Gateway / Python Runtime and returns a review-required draft. |
| `run_backtest` | `backtest:run` | Runs one bounded historical backtest; does not persist a decision. |
| `search_strategy_knowledge` | `knowledge:read` | Searches stable methodology notes, not live market evidence. |
| `search_session_document` | `document:read` | Reads only a session owned by the authenticated human user. |

The optional `MCP_SERVICE_TOKEN` is for a private, Bearer-capable MCP client.
It does **not** have `document:read`, so a shared machine token cannot inspect
any user's upload session.

## Server environment

Add these values only to the server's private environment file, never to Git:

```dotenv
# 32+ random bytes rendered as a URL-safe secret.
MCP_SERVICE_TOKEN=replace-with-a-long-random-secret
ALPHASTOCK_MCP_ALLOWED_HOSTS=alphastock.cloud,alphastock.cloud:*,www.alphastock.cloud,www.alphastock.cloud:*,localhost,localhost:*,127.0.0.1,127.0.0.1:*
ALPHASTOCK_MCP_ALLOWED_ORIGINS=https://alphastock.cloud,http://localhost:3000,http://localhost:5173
```

Restart the existing `alphastock-api.service` after deployment. Nginx already
proxies `/api/` to FastAPI, so no new Nginx location is required for
`/api/v1/mcp`.

## Client invocation and Claude boundary

For an engineering MCP client, use a standard `Authorization: Bearer ...`
header. The repository includes an actual client-side smoke check:

```powershell
$env:ALPHASTOCK_MCP_TOKEN = "<MCP_SERVICE_TOKEN-or-your-app-token>"
python scripts/mcp_smoke_client.py --url https://alphastock.cloud/api/v1/mcp/
```

A normal AlphaStock login token expires/revokes through the existing app auth
and may access only that user's claimed document sessions.

**Do not add this V1 server to Claude's web/remote connector UI yet.** Current
Claude remote connectors use an OAuth browser flow; the project currently
validates its own opaque Bearer tokens rather than providing an OAuth
Authorization Code + PKCE issuer. The endpoint is already a real remote MCP
server for clients that support a Bearer header. Adding a first-class Claude
remote connector is the next, separate auth task: implement OAuth issuer
endpoints backed by the existing AlphaStock account and bind issued scopes to
the same `MCPPrincipal` model. Do not work around this with a token in a URL or
a prompt.

## Local verification

```powershell
python -m pip install -r requirements.txt
$env:MCP_SERVICE_TOKEN = "a-long-local-test-secret"
uvicorn mcp_server:app --host 127.0.0.1 --port 8001
```

Then connect an MCP client to `http://127.0.0.1:8001/` with the same Bearer
token. In the regular application process the endpoint is instead
`http://127.0.0.1:8000/api/v1/mcp/`.
