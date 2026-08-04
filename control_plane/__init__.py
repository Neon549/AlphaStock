"""Framework-neutral runtime boundary for inbound investment-agent events."""

from control_plane.contracts import AgentEvent, AgentRunResult, TriggerType
from control_plane.gateway import Gateway

__all__ = ["AgentEvent", "AgentRunResult", "Gateway", "TriggerType"]
