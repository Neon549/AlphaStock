"""Standalone local entrypoint for the AlphaStock remote MCP server.

Production mounts the same ASGI app under the existing FastAPI service. This
entrypoint is useful for an isolated local test or a separately scaled process:

    uvicorn mcp_server:app --host 127.0.0.1 --port 8001
"""

from agent_runtime.mcp.server import mcp_asgi_app as app
