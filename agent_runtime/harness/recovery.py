"""Checkpoint, resume and logical rollback for the unified runtime."""

from __future__ import annotations

from agent_runtime.harness.state import RunState, RunStatus
from agent_runtime.harness.store import SessionStore


class RecoveryManager:
    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def checkpoint(self, state: RunState, reason: str) -> str:
        checkpoint = state.checkpoint(reason)
        self.store.save(state)
        return checkpoint.checkpoint_id

    def resume(self, run_id: str, *, profile: str) -> RunState:
        state = self.store.load(run_id)
        if state is None:
            raise KeyError(f"no durable harness session found for {run_id}")
        if state.profile != profile:
            raise ValueError(f"run {run_id} belongs to profile {state.profile}, not {profile}")
        if state.status in {RunStatus.COMPLETED, RunStatus.ABORTED}:
            raise ValueError(f"run {run_id} is terminal: {state.status.value}")
        state.status = RunStatus.RECOVERING
        state.record("resume_requested")
        state.status = RunStatus.RUNNING
        state.record("resumed", from_checkpoint=state.checkpoints[-1].checkpoint_id if state.checkpoints else None)
        self.store.save(state)
        return state

    def rollback(self, state: RunState, checkpoint_id: str) -> None:
        state.restore(checkpoint_id)
        self.store.save(state)

    def finish(self, state: RunState, status: RunStatus, *, reason: str | None = None) -> None:
        state.status = status
        state.record("run_finished", status=status.value, reason=reason)
        self.checkpoint(state, "run_finished")
