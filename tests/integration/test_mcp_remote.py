"""Remote MCP transport and safety-boundary tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from agent_runtime.mcp.server import AlphaStockTokenVerifier, build_mcp_server
from agent_runtime.mcp.service import (
    MCPAuthorizationError,
    MCPPrincipal,
    MCPToolService,
    READ_ONLY_SCOPES,
)
from control_plane.contracts import AgentRunResult, TriggerType


class _Gateway:
    def __init__(self):
        self.event = None

    def dispatch(self, event):
        self.event = event
        return AgentRunResult(
            run_id="mcp-run-1",
            route="investment_agent_loop",
            payload={
                "decision": "draft decision",
                "technical_report": "technical evidence",
                "publish_status": "published",  # MCP must downgrade this.
                "human_review_required": False,
            },
        )


class _Verifier:
    async def verify_token(self, token):
        from mcp.server.auth.provider import AccessToken

        if token != "test-token":
            return None
        return AccessToken(
            token=token,
            client_id="test-client",
            subject="alice",
            scopes=["knowledge:read"],
        )


class MCPRemoteTests(unittest.TestCase):
    def test_research_uses_mcp_event_and_never_publishes(self):
        gateway = _Gateway()
        service = MCPToolService(gateway_factory=lambda: gateway)
        principal = MCPPrincipal("alice", READ_ONLY_SCOPES)

        result = service.research_stock(
            principal,
            stock_code="600519",
            question="只看技术面",
            focus="technical",
        )

        self.assertEqual(gateway.event.trigger, TriggerType.MCP)
        self.assertEqual(gateway.event.channel, "mcp")
        self.assertEqual(result["publish_status"], "requires_human_review")
        self.assertTrue(result["human_review_required"])
        self.assertIn("cannot be published", result["notice"])

    def test_service_token_cannot_read_session_document(self):
        service = MCPToolService()
        service_principal = MCPPrincipal(
            "mcp-service", frozenset(READ_ONLY_SCOPES - {"document:read"}), is_service_account=True
        )

        with self.assertRaises(MCPAuthorizationError):
            service.search_session_document(
                service_principal,
                session_id="session-00000001",
                query="经营现金流",
            )

    def test_mcp_protocol_lists_safe_tools_and_calls_capability_tool(self):
        server = build_mcp_server(token_verifier=_Verifier())
        headers = {
            "Authorization": "Bearer test-token",
            "Host": "localhost",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        }
        with TestClient(server.streamable_http_app()) as client:
            listed = client.post(
                "/",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            self.assertEqual(listed.status_code, 200)
            names = {tool["name"] for tool in listed.json()["result"]["tools"]}
            self.assertEqual(
                names,
                {
                    "list_research_capabilities",
                    "research_stock",
                    "run_backtest",
                    "search_strategy_knowledge",
                    "search_session_document",
                },
            )

            called = client.post(
                "/",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "list_research_capabilities", "arguments": {}},
                },
            )
            self.assertEqual(called.status_code, 200)
            policy = called.json()["result"]["structuredContent"]["remote_policy"]
            self.assertTrue(policy["read_only"])
            self.assertEqual(policy["publication"], "not available through MCP")

    def test_existing_app_token_and_service_token_are_verified(self):
        verifier = AlphaStockTokenVerifier()
        with patch.dict(os.environ, {"MCP_SERVICE_TOKEN": "service-secret"}, clear=False):
            service_access = self._run(verifier.verify_token("service-secret"))
        self.assertEqual(service_access.subject, "mcp-service")
        self.assertNotIn("document:read", service_access.scopes)

        with patch("api.auth.verify_token", return_value={"valid": True, "username": "alice"}):
            user_access = self._run(verifier.verify_token("web-token"))
        self.assertEqual(user_access.subject, "alice")
        self.assertIn("document:read", user_access.scopes)

    @staticmethod
    def _run(awaitable):
        import asyncio

        return asyncio.run(awaitable)


if __name__ == "__main__":
    unittest.main()
