"""Business router composition and legacy auth fallback endpoints.

Each business area lives in ``api.routers``.  Keeping this small module as the
single composition point preserves the existing ``from api.routes import
router`` startup contract while preventing another all-in-one route file.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.auth import login as _login
from api.auth import logout as _logout
from api.auth import register as _register
from api.auth import verify_token as _verify
from api.routers.alpha import router as alpha_router
from api.routers.backtest import router as backtest_router
from api.routers.conversations import router as conversations_router
from api.routers.documents import router as documents_router
from api.routers.memory import router as memory_router
from api.routers.research import router as research_router
from api.routers.reviews import router as reviews_router


router = APIRouter()
for child_router in (
    research_router,
    backtest_router,
    documents_router,
    memory_router,
    reviews_router,
    alpha_router,
    conversations_router,
):
    router.include_router(child_router)


class AuthRequest(BaseModel):
    username: str
    password: str


class TokenRequest(BaseModel):
    token: str


@router.post("/auth/register")
def auth_register(request: AuthRequest):
    result = _register(request.username, request.password)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/auth/login")
def auth_login(request: AuthRequest):
    result = _login(request.username, request.password)
    if not result["success"]:
        raise HTTPException(status_code=401, detail=result["message"])
    return result


@router.post("/auth/verify")
def auth_verify(request: TokenRequest):
    return _verify(request.token)


@router.post("/auth/logout")
def auth_logout(request: TokenRequest):
    return _logout(request.token)
