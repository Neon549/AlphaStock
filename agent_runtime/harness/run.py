"""The single AlphaStock Harness runtime entry point."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent_runtime.harness.evidence import Evidence
from agent_runtime.governance.approval_modes import SAFE, get_approval_mode
from agent_runtime.harness.profiles import DEFAULT_PROFILES, Profile, ProfileRegistry
from agent_runtime.harness.recovery import RecoveryManager
from agent_runtime.harness.sandbox import Sandbox
from agent_runtime.harness.state import RunState, RunStatus
from agent_runtime.harness.store import SafeStore, SessionStore
from agent_runtime.harness.tools import ToolGateway
from agent_runtime.reliability import RetryBudget


@dataclass
class RunHandle:
    state: RunState
    profile: Profile
    recovery: RecoveryManager
    sandbox: Sandbox
    evidence: Evidence
    tools: ToolGateway
    retry_budget: RetryBudget

    def record(self, event: str, **detail: Any) -> dict[str, Any]:
        return self.state.record(event, **detail)

    def checkpoint(self, reason: str) -> str:
        return self.recovery.checkpoint(self.state, reason)

    def rollback(self, checkpoint_id: str) -> None:
        self.recovery.rollback(self.state, checkpoint_id)

    def complete(self, *, reason: str | None = None) -> None:
        self.recovery.finish(self.state, RunStatus.COMPLETED, reason=reason)

    def abort(self, *, reason: str) -> None:
        self.recovery.finish(self.state, RunStatus.ABORTED, reason=reason)

    def fail(self, *, reason: str) -> None:
        self.recovery.finish(self.state, RunStatus.FAILED, reason=reason)

    def summary(self) -> dict[str, Any]:
        return {
            **self.state.summary(),
            "retry_budget": self.retry_budget.summary(),
            "sandbox_mode": self.state.data.get("approval_mode") or self.sandbox.mode,
        }


class Harness:
    def __init__(
        self,
        *,
        profiles: ProfileRegistry | None = None,
        store: SessionStore | None = None,
        sandbox: Sandbox | None = None,
        evidence: Evidence | None = None,
        tools: ToolGateway | None = None,
    ) -> None:
        self.profiles = profiles or DEFAULT_PROFILES
        self.store = store or SafeStore()
        self.recovery = RecoveryManager(self.store)
        self.sandbox = sandbox or Sandbox()
        self.evidence = evidence or Evidence()
        self.tools = tools or ToolGateway()

    def open(
        self,
        profile_name: str,
        data: Mapping[str, Any] | None = None,
        *,
        run_id: str | None = None,
        resume: bool = False,
    ) -> RunHandle:
        profile = self.profiles.get(profile_name)
        if resume:
            if not run_id:
                raise ValueError("resume requires run_id")
            state = self.recovery.resume(run_id, profile=profile.name)
        else:
            run_data = dict(data or {})
            actor_id = str(run_data.get("actor_id") or "").strip()
            if actor_id and "approval_mode" not in run_data:
                # The approval API owns the three user modes and expiry rules.
                # Its read path fails closed to safe mode if persistence is down.
                run_data["approval_mode"] = get_approval_mode(actor_id).get("mode", SAFE)
            state = RunState.create(profile.name, run_data, run_id=run_id)
            state.status = RunStatus.RUNNING
            state.record(
                "run_started",
                profile=profile.name,
                sandbox_mode=state.data.get("approval_mode") or self.sandbox.mode,
            )
        retry_budget = RetryBudget()
        prior_budget = state.data.get("retry_budget") or {}
        retry_budget.retries_used = int(prior_budget.get("retries_used") or 0)
        retry_budget.delay_used_seconds = float(prior_budget.get("delay_used_seconds") or 0.0)
        handle = RunHandle(
            state=state,
            profile=profile,
            recovery=self.recovery,
            sandbox=self.sandbox,
            evidence=self.evidence,
            tools=self.tools,
            retry_budget=retry_budget,
        )
        if not resume:
            handle.checkpoint("run_started")
        return handle


_DEFAULT: Harness | None = None


def default_harness() -> Harness:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Harness()
    return _DEFAULT


# One product runtime, not one Harness per business role.  Profiles compose
# Research and Investment behaviour on the same RuntimeKernel.
RuntimeKernel = Harness
AlphaStockHarness = Harness
