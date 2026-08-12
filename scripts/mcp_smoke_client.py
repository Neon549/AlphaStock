"""Minimal authenticated MCP client for local or deployed smoke checks.

Example (PowerShell):

    $env:ALPHASTOCK_MCP_TOKEN = "..."
    python scripts/mcp_smoke_client.py --url https://alphastock.cloud/api/v1/mcp/
"""

from __future__ import annotations

import argparse
import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def _run(url: str, token: str, query: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Available tools:", ", ".join(tool.name for tool in tools.tools))
            result = await session.call_tool("search_strategy_knowledge", {"query": query, "top_k": 2})
            print("search_strategy_knowledge:")
            print(result.structuredContent or result.content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the AlphaStock Streamable HTTP MCP endpoint.")
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/v1/mcp/")
    parser.add_argument("--query", default="KDJ 最大回撤 如何解读")
    args = parser.parse_args()
    token = os.getenv("ALPHASTOCK_MCP_TOKEN", "").strip()
    if not token:
        raise SystemExit("Set ALPHASTOCK_MCP_TOKEN to a valid app token or MCP_SERVICE_TOKEN first.")
    asyncio.run(_run(args.url, token, args.query))


if __name__ == "__main__":
    main()
