"""Transport adapters that produce AgentEvent; they never call LLMs/tools."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from control_plane.contracts import AgentEvent, TriggerType
from control_plane.source_registry import SourceObservation, revision_event_id, revision_dedupe_key


def cli_event(content: str, *, session_id: str | None = None, actor_id: str | None = None, model: str = "smart") -> AgentEvent:
    return AgentEvent(TriggerType.CLI, content, session_id=session_id, actor_id=actor_id, channel="cli", metadata={"model": model})


def cron_event(job_name: str, content: str, *, scheduled_at: datetime | None = None) -> AgentEvent:
    due = (scheduled_at or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    event_id = hashlib.sha256(f"cron:{job_name}:{due}".encode("utf-8")).hexdigest()
    return AgentEvent(TriggerType.CRON, content, channel="cron", event_id=event_id, metadata={"job_name": job_name, "scheduled_at": due})


def source_changed_event(
    observation: SourceObservation,
    *,
    content: str | None = None,
    actor_id: str | None = None,
    channel: str = "source-watcher",
    model: str = "smart",
    extra_metadata: dict[str, Any] | None = None,
) -> AgentEvent:
    """Convert a detected source revision into a stable, deduplicable event.

    The event identity is derived from ``source_id`` plus the observed
    revision/hash.  A webhook retry and a cron retry therefore enter Gateway
    with the same event ID, while a genuinely new source revision creates a
    new event.
    """

    if not observation.changed:
        raise ValueError("an unchanged source cannot emit source_changed_event")
    metadata = observation.to_metadata()
    metadata.update({"event_type": "source.changed", "model": model})
    if extra_metadata:
        metadata.update(extra_metadata)
    message = content or (
        f"刷新数据源 {observation.source_id}"
        + (f"，影响标的 {', '.join(observation.affected_symbols)}" if observation.affected_symbols else "")
    )
    return AgentEvent(
        trigger=TriggerType.SOURCE_CHANGE,
        content=message,
        actor_id=actor_id,
        channel=channel,
        event_id=observation.event_id,
        metadata=metadata,
    )


def heartbeat_event(*, job_name: str = "agent-heartbeat", observed_at: datetime | None = None) -> AgentEvent:
    """Create a lightweight liveness event; it carries no user data."""

    observed = (observed_at or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    event_id = hashlib.sha256(f"heartbeat:{job_name}:{observed}".encode("utf-8")).hexdigest()
    return AgentEvent(
        trigger=TriggerType.HEARTBEAT,
        content="agent heartbeat",
        channel="heartbeat",
        event_id=event_id,
        metadata={"job_name": job_name, "observed_at": observed},
    )


def webhook_event(payload: dict[str, Any], signature: str, secret: str) -> AgentEvent:
    """Verify a signed JSON payload before normalising it into an event."""
    if not secret:
        raise PermissionError("webhook secret is not configured")
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.removeprefix("sha256="), expected):
        raise PermissionError("invalid webhook signature")
    webhook_type = str(payload.get("type") or "generic")
    is_source_change = webhook_type in {
        "source.changed",
        "financial_report.changed",
        "news.changed",
        "market_data.changed",
    }
    content = str(payload.get("content") or "").strip()
    if is_source_change:
        source_id = str(payload.get("source_id") or "").strip()
        source_type = str(payload.get("source_type") or webhook_type).strip()
        version = str(payload.get("source_version") or "").strip() or None
        content_hash = str(payload.get("content_hash") or "").strip() or None
        symbols = payload.get("affected_symbols") or []
        if isinstance(symbols, str):
            symbols = [symbols]
        symbols = [str(item).strip() for item in symbols if str(item).strip()]
        if not source_id or not symbols or not (version or content_hash):
            raise ValueError(
                "source change webhook requires source_id, affected_symbols and source_version or content_hash"
            )
        dedupe_key = revision_dedupe_key(source_id, version, content_hash)
        event_id = revision_event_id(dedupe_key)
        content = content or f"刷新数据源 {source_id}，影响标的 {', '.join(symbols)}"
        metadata = {
            "model": str(payload.get("model") or "smart"),
            "webhook_type": webhook_type,
            "event_type": "source.changed",
            "source_id": source_id,
            "source_type": source_type,
            "source_version": version,
            "content_hash": content_hash,
            "affected_symbols": symbols,
            "source_endpoint": payload.get("source_endpoint"),
            "dedupe_key": dedupe_key,
        }
        return AgentEvent(
            TriggerType.SOURCE_CHANGE,
            content,
            session_id=payload.get("session_id"),
            actor_id=payload.get("actor_id"),
            channel=str(payload.get("channel") or "webhook"),
            event_id=event_id,
            metadata=metadata,
        )
    if not content:
        raise ValueError("webhook payload.content is required")
    return AgentEvent(
        TriggerType.WEBHOOK, content, session_id=payload.get("session_id"),
        actor_id=payload.get("actor_id"), channel=str(payload.get("channel") or "webhook"),
        event_id=str(payload.get("event_id") or hashlib.sha256(raw).hexdigest()),
        metadata={"model": str(payload.get("model") or "smart"), "webhook_type": webhook_type},
    )
