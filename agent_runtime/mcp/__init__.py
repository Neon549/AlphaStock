"""Remote MCP boundary for the AlphaStock research runtime.

The package deliberately contains adapters only. Business capabilities remain
in the Gateway, Python Runtime, document-RAG skill and backtest service; MCP
only authenticates a remote client and exposes a small, read-only tool surface.
"""
