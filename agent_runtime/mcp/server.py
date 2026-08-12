"""Standards-based Streamable HTTP MCP server mounted by ``main.py``.

It exposes a small adapter layer over :mod:`agent_runtime.mcp.service`. The
transport authenticates the project's existing opaque login token (or a
separately configured service integration token), then delegates to the
existing Gateway/Python Runtime instead of duplicating business logic.
"""

from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone
from typing import Any

from agent_runtime.mcp.service import (
    MCPAuthorizationError,
    MCPPrincipal,
    MCPToolService,
    READ_ONLY_SCOPES,
)


def _csv_env(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def _transport_security():
    """Configure a tight host/origin allowlist for the deployed HTTPS endpoint."""

    from mcp.server.transport_security import TransportSecuritySettings

    return TransportSecuritySettings(
        allowed_hosts=_csv_env(
            "ALPHASTOCK_MCP_ALLOWED_HOSTS",
            "alphastock.cloud,alphastock.cloud:*,www.alphastock.cloud,www.alphastock.cloud:*,"
            "localhost,localhost:*,127.0.0.1,127.0.0.1:*",
        ),
        allowed_origins=_csv_env(
            "ALPHASTOCK_MCP_ALLOWED_ORIGINS",
            "https://alphastock.cloud,http://localhost:3000,http://localhost:5173",
        ),
    )


def _auth_settings():
    """Describe this endpoint as an MCP protected resource.

    The V1 verifier accepts the project's existing opaque bearer tokens. The
    explicit resource metadata keeps the transport compatible with standard
    OAuth-capable MCP clients once the application adds its OAuth issuer.
    """

    from mcp.server.auth.settings import AuthSettings

    return AuthSettings(
        issuer_url=os.getenv("ALPHASTOCK_MCP_AUTH_ISSUER_URL", "https://alphastock.cloud"),
        resource_server_url=os.getenv(
            "ALPHASTOCK_MCP_RESOURCE_URL", "https://alphastock.cloud/api/v1/mcp/"
        ),
        required_scopes=[],
    )


class AlphaStockTokenVerifier:
    """Bridge existing opaque web tokens into the official MCP Bearer verifier.

    ``MCP_SERVICE_TOKEN`` is optional and intentionally grants only the same
    read-only remote scopes. It is for a personal Claude Desktop integration;
    it never receives access to a human user's uploaded documents.
    """

    async def verify_token(self, token: str):
        from mcp.server.auth.provider import AccessToken

        service_token = os.getenv("MCP_SERVICE_TOKEN", "").strip()
        if service_token and hmac.compare_digest(token, service_token):
            return AccessToken(
                token=token,
                client_id="alphastock-mcp-service",
                subject="mcp-service",
                scopes=sorted(READ_ONLY_SCOPES - {"document:read"}),
                claims={"is_service_account": True},
            )

        # Login tokens are stored as digests with expiry/revocation in
        # PostgreSQL. Reuse that implementation instead of creating a second
        # password or API-key database.
        from api.auth import verify_token

        identity = verify_token(token)
        if not identity.get("valid"):
            return None
        expires_at = identity.get("expires_at")
        epoch: int | None = None
        if isinstance(expires_at, datetime):
            epoch = int(expires_at.astimezone(timezone.utc).timestamp())
        return AccessToken(
            token=token,
            client_id="alphastock-web-user",
            subject=str(identity["username"]),
            scopes=sorted(READ_ONLY_SCOPES),
            expires_at=epoch,
            claims={"is_service_account": False},
        )


def _current_principal() -> MCPPrincipal:
    from mcp.server.auth.middleware.auth_context import get_access_token

    access_token = get_access_token()
    if access_token is None or not access_token.subject:
        raise MCPAuthorizationError("an authenticated Bearer token is required")
    return MCPPrincipal(
        actor_id=access_token.subject,
        scopes=frozenset(access_token.scopes),
        is_service_account=bool((access_token.claims or {}).get("is_service_account")),
    )


def build_mcp_server(
    *,
    tool_service: MCPToolService | None = None,
    token_verifier: Any | None = None,
):
    """Create the mounted server; injection points keep integration tests offline."""

    from mcp.server.fastmcp import FastMCP

    service = tool_service or MCPToolService()
    server = FastMCP(
        "AlphaStock Research MCP",
        instructions=(
            "Read-only A-share research tools. Treat all outputs as research drafts, "
            "retain cited evidence and never interpret an output as a trade instruction. "
            "Publication approval and trade execution are intentionally unavailable."
        ),
        json_response=True,
        stateless_http=True,
        streamable_http_path="/",
        max_request_body_size=1 * 1024 * 1024,
        token_verifier=token_verifier or AlphaStockTokenVerifier(),
        auth=_auth_settings(),
        transport_security=_transport_security(),
    )

    @server.tool()
    def list_research_capabilities() -> dict[str, Any]:
        """List remote-safe AlphaStock skills and immutable MCP safety boundaries."""

        return service.list_capabilities(_current_principal())

    @server.tool()
    def research_stock(
        stock_code: str,
        question: str = "",
        focus: str = "all",
        session_id: str | None = None,
        model_profile: str = "smart",
    ) -> dict[str, Any]:
        """Create a governed A-share research draft; it always requires human review."""

        return service.research_stock(
            _current_principal(),
            stock_code=stock_code,
            question=question,
            focus=focus,
            session_id=session_id,
            model_profile=model_profile,
        )

    @server.tool()
    def run_backtest(
        stock_code: str,
        strategy: str = "kdj_macd",
        start_date: str = "20220101",
        end_date: str = "20261231",
        initial_cash: float = 100000.0,
    ) -> dict[str, Any]:
        """Run one bounded historical strategy backtest; no portfolio is changed."""

        return service.run_backtest(
            _current_principal(),
            stock_code=stock_code,
            strategy=strategy,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
        )

    @server.tool()
    def search_strategy_knowledge(query: str, top_k: int = 3) -> dict[str, Any]:
        """Retrieve stable backtest methodology guidance, not live market facts."""

        return service.search_strategy_knowledge(_current_principal(), query=query, top_k=top_k)

    @server.tool()
    def search_session_document(session_id: str, query: str, top_k: int = 3) -> dict[str, Any]:
        """Retrieve only the authenticated user's own uploaded document evidence."""

        return service.search_session_document(
            _current_principal(),
            session_id=session_id,
            query=query,
            top_k=top_k,
        )

    return server


mcp_server = build_mcp_server()
# ``main.py`` mounts this at ``/api/v1/mcp``. Since the transport path is
# configured as ``/``, the public remote endpoint is exactly that URL.
mcp_asgi_app = mcp_server.streamable_http_app()
