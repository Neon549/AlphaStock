"""User-selectable approval modes and the three-layer risk funnel.

The mode controls workflow friction; it never disables hard safety rules. The
three layers are:

1. hard block: forbidden market facts, secrets, privacy data and unsupported
   investment claims are rejected before a candidate is stored;
2. risk routing: low-risk operating lessons may be automated according to the
   selected mode, while medium/high-risk work is grouped for one confirmation;
3. elevated access: ``full_access`` requires an explicit, expiring user
   acknowledgement before it can auto-handle medium-risk candidates.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any


SAFE = "safe"
ASSIST = "assist"
FULL_ACCESS = "full_access"
MODES = frozenset({SAFE, ASSIST, FULL_ACCESS})
RISKS = frozenset({"low", "medium", "high"})

MODE_METADATA: dict[str, dict[str, Any]] = {
    SAFE: {
        "label": "安全模式",
        "description": "所有长期记忆候选都保留为 pending，等待用户显式审核。",
        "requires_confirmation": False,
    },
    ASSIST: {
        "label": "帮我审批",
        "description": "系统自动处理低风险经验，其余候选一次批量确认。",
        "requires_confirmation": False,
    },
    FULL_ACCESS: {
        "label": "完全访问权限",
        "description": "在一次高风险确认和短时授权内自动处理低/中风险经验；硬阻断与高风险仍需拦截。",
        "requires_confirmation": True,
    },
}


class ApprovalModeConfirmationRequired(PermissionError):
    """Raised when elevated mode is requested without explicit acknowledgement."""


def classify_memory_candidate(*, category: str, title: str, content: str) -> str:
    """Conservatively classify reusable operating knowledge for automation."""

    text = f"{category} {title} {content}".casefold()
    high_hints = (
        "买入", "卖出", "加仓", "减仓", "目标价", "仓位", "收益", "交易指令",
        "buy", "sell", "position size", "price target", "trade instruction",
    )
    if any(hint in text for hint in high_hints):
        return "high"
    if category.casefold() in {"governance", "research", "backtest"}:
        return "medium"
    return "low"


def route_memory_candidate(mode: str, risk: str) -> str:
    """Return the action for the non-hard-blocked risk layer.

    ``batch_confirmation`` means the UI can present one confirmation for a
    group. It is intentionally not an instruction to prompt once per item.
    """

    if mode not in MODES:
        mode = SAFE
    if risk not in RISKS:
        risk = "high"
    if mode == SAFE:
        return "manual_review"
    if mode == ASSIST:
        return "auto_approve" if risk == "low" else "batch_confirmation"
    if risk in {"low", "medium"}:
        return "auto_approve"
    return "batch_confirmation"


def _parse_expiry(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def get_approval_mode(actor_id: str) -> dict[str, Any]:
    """Load one actor's mode; unavailable persistence fails closed to safe mode."""

    from db import execute

    try:
        row = execute(
            """SELECT mode, elevated_confirmed_at, elevated_expires_at, updated_at
               FROM agent_approval_modes WHERE actor_id = %s""",
            (actor_id,), fetch="one",
        )
    except Exception:
        row = None
    if not row or row[0] not in MODES:
        mode, confirmed_at, expires_at, updated_at = SAFE, None, None, None
    else:
        mode, confirmed_at, expires_at, updated_at = row
        if mode == FULL_ACCESS and (
            not confirmed_at or not expires_at or _parse_expiry(expires_at) <= datetime.now(timezone.utc)
        ):
            mode, confirmed_at, expires_at = SAFE, None, None
    return {
        "mode": mode,
        **MODE_METADATA[mode],
        "actor_id": actor_id,
        "elevated_confirmed_at": confirmed_at,
        "elevated_expires_at": expires_at,
        "updated_at": updated_at,
    }


def set_approval_mode(
    actor_id: str,
    mode: str,
    *,
    confirm_risk: bool = False,
    ttl_minutes: int | None = None,
) -> dict[str, Any]:
    """Set an actor mode, requiring explicit acknowledgement for full access."""

    mode = (mode or "").strip().lower()
    if mode not in MODES:
        raise ValueError("mode must be safe, assist or full_access")
    if mode == FULL_ACCESS and not confirm_risk:
        raise ApprovalModeConfirmationRequired(
            "full_access requires explicit confirmation of elevated risks"
        )

    now = datetime.now(timezone.utc)
    confirmed_at = now if mode == FULL_ACCESS else None
    expires_at = None
    if mode == FULL_ACCESS:
        bounded_minutes = max(5, min(int(ttl_minutes or os.getenv("FULL_ACCESS_TTL_MINUTES", "30")), 120))
        expires_at = now + timedelta(minutes=bounded_minutes)

    from db import execute

    execute(
        """INSERT INTO agent_approval_modes
           (actor_id, mode, elevated_confirmed_at, elevated_expires_at, updated_at)
           VALUES (%s, %s, %s, %s, NOW())
           ON CONFLICT (actor_id) DO UPDATE SET
             mode=EXCLUDED.mode,
             elevated_confirmed_at=EXCLUDED.elevated_confirmed_at,
             elevated_expires_at=EXCLUDED.elevated_expires_at,
             updated_at=NOW()""",
        (actor_id, mode, confirmed_at, expires_at),
    )
    return get_approval_mode(actor_id)
