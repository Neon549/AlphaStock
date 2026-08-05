"""Alpha factor scoring endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from api.security import require_actor
from control_plane.security import SecurityOperation, authorize_operation


router = APIRouter(tags=["alpha"])


class AlphaRequest(BaseModel):
    stocks: Optional[list] = None
    min_score: float = 60
    top_n: int = 20
    sector: Optional[str] = None


class SingleAlphaRequest(BaseModel):
    stock_code: str
    stock_name: str = ""


def _authorize(tool: str, target: str, actor_id: str) -> None:
    try:
        authorize_operation(
            SecurityOperation(tool=tool, target=target, actor_id=actor_id), mode="auto"
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="operation is not permitted") from exc


@router.post("/alpha/score")
def alpha_score(
    request: AlphaRequest,
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    actor_id = require_actor(x_auth_token=x_auth_token, authorization=authorization)
    _authorize("alpha", "score", actor_id)
    if not 1 <= request.top_n <= 50 or not 0 <= request.min_score <= 100:
        raise HTTPException(status_code=400, detail="invalid alpha score limits")
    try:
        from backtest.alpha_factor import batch_score
        from backtest.stock_universe import STOCK_UNIVERSE, get_dynamic_universe

        if request.stocks:
            stock_list = [(stock[0], stock[1]) for stock in request.stocks]
        elif request.sector and request.sector in STOCK_UNIVERSE:
            stock_list = list(STOCK_UNIVERSE[request.sector].items())
        else:
            stock_list = get_dynamic_universe(max_stocks=200, use_cache=True)
        scores = batch_score(stock_list=stock_list, min_score=request.min_score, top_n=request.top_n)
        return {
            "total_scored": len(stock_list),
            "qualified": len(scores),
            "min_score": request.min_score,
            "results": [score.to_dict() for score in scores],
            "status": "success",
        }
    except Exception:
        raise HTTPException(status_code=500, detail="alpha scoring failed")


@router.post("/alpha/single")
def alpha_single(
    request: SingleAlphaRequest,
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    actor_id = require_actor(x_auth_token=x_auth_token, authorization=authorization)
    _authorize("alpha", "single", actor_id)
    try:
        from backtest.alpha_factor import format_score_report, score_stock

        score = score_stock(request.stock_code, request.stock_name or request.stock_code)
        if score.error:
            raise HTTPException(status_code=400, detail=f"打分失败：{score.error}")
        return {**score.to_dict(), "report": format_score_report(score), "status": "success"}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="alpha scoring failed")
