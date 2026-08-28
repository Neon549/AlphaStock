import os
import sys
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# Database initialisation happens before the FastAPI server owns stdout.
# Configure Windows consoles first so startup diagnostics never fail on emoji.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from db import init_db

init_db()  # 建表，幂等，重复执行无害

app = FastAPI(
    title="AlphaStock · 智能投研助手",
    description="分析基本面、技术面、情绪面，结合Alpha因子回测，辅助A股交易决策",
    version="2.0.0",
)

_cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "ALPHASTOCK_CORS_ORIGINS",
        "https://alphastock.cloud,http://localhost:3000,http://localhost:5173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type", "Authorization", "X-Auth-Token", "Idempotency-Key",
        "Last-Event-ID", "Mcp-Method", "Mcp-Name", "Mcp-Protocol-Version", "Mcp-Session-Id",
    ],
    expose_headers=["Mcp-Session-Id"],
)

# The MCP server is an ASGI sub-application. Business modules are not loaded
# here: the remote adapter imports the Gateway lazily only when a bounded tool
# is invoked. ``streamable_http_path='/'`` makes the mounted URL itself the
# protocol endpoint: /api/v1/mcp.
from agent_runtime.mcp.server import mcp_asgi_app, mcp_server
app.mount("/api/v1/mcp", mcp_asgi_app)


@app.on_event("startup")
async def start_mcp_session_manager():
    """Mounted Starlette apps do not run their own lifespan automatically."""

    manager = mcp_server.session_manager.run()
    await manager.__aenter__()
    app.state.mcp_session_manager = manager


@app.on_event("shutdown")
async def stop_mcp_session_manager():
    manager = getattr(app.state, "mcp_session_manager", None)
    if manager is not None:
        await manager.__aexit__(None, None, None)


@app.middleware("http")
async def request_size_guard(request: Request, call_next):
    """Reject oversized bodies before FastAPI parses JSON or multipart data."""

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > 25 * 1024 * 1024:
                return JSONResponse(status_code=413, content={"detail": "request body is too large"})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "invalid content length"})
    return await call_next(request)

# The business router is imported after the HTTP server has started so a slow
# RAG/LLM dependency cannot block the basic health endpoint.  Keep its state
# observable: the previous broad exception handler made a failed import look
# exactly like a normal 404 to the web client.
_business_router_state = {
    "state": "initializing",
    "error_type": None,
    "error_detail": None,
    "news_index_state": "pending",
}

# Health routes stay independent from the heavy RAG/LLM imports. This lets the
# orchestrator distinguish a live process from one that is ready for traffic.
from api.health import HealthService, build_health_router

app.include_router(build_health_router(HealthService(_business_router_state)))


@app.get("/")
def root():
    return {"message": "AlphaStock · 智能投研助手", "docs": "/docs", "version": "2.0.0"}


@app.get("/health")
def health():
    """Legacy liveness alias. New infrastructure should use /health/live."""
    return {"status": "ok"}


@app.get("/api/v1/health")
def api_health():
    """Health contract used by the reverse proxy and frontend diagnostics."""
    return {
        "status": "ok",
        "business_router": _business_router_state["state"],
        "business_router_error_type": _business_router_state["error_type"],
        "business_router_error_detail": _business_router_state["error_detail"],
        "news_index": _business_router_state["news_index_state"],
    }


@app.on_event("startup")
async def startup_event():
    """同步加载 auth 路由，后台加载重依赖路由"""
    import threading

    # auth 路由立即加载，保证登录注册随时可用
    try:
        from api.auth_routes import router as auth_router
        app.include_router(auth_router, prefix="/api/v1")
        print("[Startup] Auth 路由加载完成 ✅")
    except Exception:
        pass  # auth_routes 不存在时回退到 routes.py 里的 auth

    try:
        from api.auth_google import router as google_router
        app.include_router(google_router, prefix="/api/v1")
        print("[Startup] Google Auth 路由加载完成 ✅")
    except Exception as e:
        print(f"[Startup] Google Auth 路由加载失败: {e}")

    def _heavy_init():
        try:
            # 延迟加载业务路由（包含 langchain/langgraph 等重依赖）
            from api.routes import router
            app.include_router(router, prefix="/api/v1")
            _business_router_state["state"] = "ready"
            print("[Startup] 业务路由加载完成 ✅")
        except Exception as e:
            _business_router_state["state"] = "failed"
            _business_router_state["error_type"] = type(e).__name__
            # Keep public diagnostics bounded to import errors only.  These
            # identify a missing package/symbol without exposing config or a
            # traceback from the server process.
            _business_router_state["error_detail"] = (
                str(e)[:240] if isinstance(e, ImportError) else None
            )
            print(f"[Startup] 业务路由加载失败: {e}")
            import traceback
            traceback.print_exc()
            return

        try:
            # 新闻系统
            from rag.news_indexer import start_news_system
            start_news_system(bulk_first=True, stream_interval=5, cleanup_hour=2)
            _business_router_state["news_index_state"] = "ready"
            print("[Startup] 新闻系统启动完成 ✅")
        except Exception as e:
            _business_router_state["news_index_state"] = "failed"
            print(f"[Startup] 新闻系统启动失败（业务路由继续可用）: {e}")
            import traceback
            traceback.print_exc()

    threading.Thread(target=_heavy_init, daemon=True).start()
    print("[Startup] FastAPI已就绪，业务模块后台加载中...")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
