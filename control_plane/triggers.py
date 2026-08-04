"""Transport adapters that produce AgentEvent; they never call LLMs/tools."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from control_plane.contracts import AgentEvent, TriggerType


def cli_event(content: str, *, session_id: str | None = None, actor_id: str | None = None, model: str = "smart") -> AgentEvent:
    return AgentEvent(TriggerType.CLI, content, session_id=session_id, actor_id=actor_id, channel="cli", metadata={"model": model})


def cron_event(job_name: str, content: str, *, scheduled_at: datetime | None = None) -> AgentEvent:
    due = (scheduled_at or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    event_id = hashlib.sha256(f"cron:{job_name}:{due}".encode("utf-8")).hexdigest()
    return AgentEvent(TriggerType.CRON, content, channel="cron", event_id=event_id, metadata={"job_name": job_name, "scheduled_at": due})


def webhook_event(payload: dict[str, Any], signature: str, secret: str) -> AgentEvent:
    """Verify a signed JSON payload before normalising it into an event."""
    if not secret:
        raise PermissionError("webhook secret is not configured")
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.removeprefix("sha256="), expected):
        raise PermissionError("invalid webhook signature")
    content = str(payload.get("content") or "").strip()
    if not content:
        raise ValueError("webhook payload.content is required")
    return AgentEvent(
        TriggerType.WEBHOOK, content, session_id=payload.get("session_id"),
        actor_id=payload.get("actor_id"), channel=str(payload.get("channel") or "webhook"),
        event_id=str(payload.get("event_id") or hashlib.sha256(raw).hexdigest()),
        metadata={"model": str(payload.get("model") or "smart"), "webhook_type": payload.get("type", "generic")},
    )
