"""Small, serialisable state primitives for the unified agent runtime.

The runtime keeps an append-only event list.  A checkpoint restores the
*materialised* state without deleting that audit trail, which makes rollback
and fork decisions reviewable.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python 3.10 compatibility.
    class StrEnum(str, Enum):
        pass


FORMAT = "alphastock-harness/v1"


class RunStatus(StrEnum):
    NEW = "new"
    RUNNING = "running"
    PAUSED = "paused"
    RECOVERING = "recovering"
    REPLANNING = "replanning"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_value(value: Any) -> Any:
    """Return a JSON-safe snapshot without retaining live client objects."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_value(item) for item in value]
    return str(value)


@dataclass
class Checkpoint:
    checkpoint_id: str
    reason: str
    event_seq: int
    step: int
    status: str
    data: dict[str, Any]
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Checkpoint":
        return cls(
            checkpoint_id=str(value["checkpoint_id"]),
            reason=str(value.get("reason") or "unknown"),
            event_seq=int(value.get("event_seq") or 0),
            step=int(value.get("step") or 0),
            status=str(value.get("status") or RunStatus.RUNNING.value),
            data=dict(value.get("data") or {}),
            created_at=str(value.get("created_at") or _now()),
        )


@dataclass
class RunState:
    run_id: str
    profile: str
    data: dict[str, Any] = field(default_factory=dict)
    status: RunStatus = RunStatus.NEW
    step: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    @classmethod
    def create(cls, profile: str, data: Mapping[str, Any] | None = None, *, run_id: str | None = None) -> "RunState":
        return cls(
            run_id=run_id or str(uuid4()),
            profile=profile,
            data=json_value(dict(data or {})),
        )

    def record(self, event: str, **detail: Any) -> dict[str, Any]:
        item = {
            "seq": len(self.events) + 1,
            "event": event,
            "time": _now(),
            **json_value(detail),
        }
        self.events.append(item)
        self.updated_at = item["time"]
        return item

    def checkpoint(self, reason: str) -> Checkpoint:
        snapshot = Checkpoint(
            checkpoint_id=f"cp-{len(self.checkpoints) + 1:04d}",
            reason=reason,
            event_seq=len(self.events),
            step=self.step,
            status=self.status.value,
            data=deepcopy(json_value(self.data)),
        )
        self.checkpoints.append(snapshot)
        self.record("checkpoint", checkpoint_id=snapshot.checkpoint_id, reason=reason, step=self.step)
        return snapshot

    def restore(self, checkpoint_id: str) -> Checkpoint:
        checkpoint = next((item for item in self.checkpoints if item.checkpoint_id == checkpoint_id), None)
        if checkpoint is None:
            raise KeyError(f"unknown checkpoint: {checkpoint_id}")
        self.data = deepcopy(checkpoint.data)
        self.step = checkpoint.step
        self.status = RunStatus.RUNNING
        self.record("rollback", checkpoint_id=checkpoint_id, restored_event_seq=checkpoint.event_seq)
        return checkpoint

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "run_id": self.run_id,
            "profile": self.profile,
            "status": self.status.value,
            "step": self.step,
            "data": json_value(self.data),
            "events": json_value(self.events),
            "checkpoints": [item.to_dict() for item in self.checkpoints],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunState":
        if str(value.get("format") or "") != FORMAT:
            raise ValueError("unsupported harness session format")
        try:
            status = RunStatus(str(value.get("status") or RunStatus.NEW.value))
        except ValueError:
            status = RunStatus.FAILED
        return cls(
            run_id=str(value["run_id"]),
            profile=str(value["profile"]),
            data=dict(value.get("data") or {}),
            status=status,
            step=int(value.get("step") or 0),
            events=list(value.get("events") or []),
            checkpoints=[Checkpoint.from_dict(item) for item in value.get("checkpoints") or []],
            created_at=str(value.get("created_at") or _now()),
            updated_at=str(value.get("updated_at") or _now()),
        )

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "profile": self.profile,
            "status": self.status.value,
            "step": self.step,
            "checkpoint_count": len(self.checkpoints),
            "latest_checkpoint": self.checkpoints[-1].checkpoint_id if self.checkpoints else None,
        }
