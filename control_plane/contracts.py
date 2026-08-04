"""Stable contracts between trigger, gateway and agent runtime.

These are deliberately plain Python dataclasses.  The runtime can therefore
start from FastAPI today and later accept cron, CLI, webhooks or chat channels
without making those delivery details part of the investment workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


class TriggerType(StrEnum):
    MESSAGE = "message"
    CLI = "cli"
    HTTP = "http"
    CRON = "cron"
    WEBHOOK = "webhook"
    HOOK = "hook"


@dataclass(frozen=True)
class AgentEvent:
    """Normalised, authenticated input.  Gateway never puts transport objects here."""

    trigger: TriggerType
    content: str
    session_id: str | None = None
    actor_id: str | None = None
    channel: str = "web"
    event_id: str = field(default_factory=lambda: str(uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRunResult:
    """Transport-independent runtime output, including a small audit trace."""

    run_id: str
    route: str
    payload: dict[str, Any]
    trace: list[dict[str, Any]] = field(default_factory=list)
