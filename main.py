import os
import sys
import uvicorn
from fastapi import FastAPI
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"message": "AlphaStock · 智能投研助手", "docs": "/docs", "version": "2.0.0"}


@app.get("/health")
def health():
    return {"status": "ok"}


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
            print("[Startup] 业务路由加载完成 ✅")

            # 新闻系统
            from rag.news_indexer import start_news_system
            start_news_system(bulk_first=True, stream_interval=5, cleanup_hour=2)
            print("[Startup] 新闻系统启动完成 ✅")
        except Exception as e:
            print(f"[Startup] 后台初始化失败: {e}")
            import traceback
            traceback.print_exc()

    threading.Thread(target=_heavy_init, daemon=True).start()
    print("[Startup] FastAPI已就绪，业务模块后台加载中...")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
