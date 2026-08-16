"""Fail-closed application sandbox for Harness tool calls.

It is intentionally an application boundary, not a claim of OS isolation.
AlphaStock profiles expose no raw shell, publishing, or trading capability;
those remain denied even in the most permissive user mode.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runtime.harness.profiles import Profile, ToolSpec
from control_plane.security import (
    PermissionMode,
    SecurityOperation,
    evaluate_permission,
)


@dataclass(frozen=True)
class SandboxDecision:
    allowed: bool
    reason: str
    mode: str
    stage: str


class PermissionManager:
    """One policy boundary for every profile and every tool invocation."""

    _IMMUTABLE_DENY = {"shell", "bash", "command", "exec", "publish", "trade", "delete", "write", "edit"}

    def __init__(self, mode: str | None = None, *, root: Path | None = None) -> None:
        selected = (mode or os.getenv("ALPHASTOCK_SANDBOX_MODE", "safe")).strip().lower()
        self.mode = selected if selected in {"safe", "assist", "full_access"} else "safe"
        self.root = (root or Path(os.getenv("ALPHASTOCK_PROJECT_ROOT", Path.cwd()))).resolve()

    def check(
        self,
        profile: Profile,
        tool: ToolSpec | None,
        *,
        granted: set[str],
        arguments: dict[str, Any] | None = None,
        session_id: str | None = None,
        actor_id: str | None = None,
        approval_mode: str | None = None,
    ) -> SandboxDecision:
        selected_mode = (approval_mode or self.mode).strip().lower()
        selected_mode = selected_mode if selected_mode in {"safe", "assist", "full_access"} else "safe"
        registered = profile.tool(tool.name) if tool is not None else None
        if registered is None:
            return SandboxDecision(False, "tool is outside the selected profile", selected_mode, "profile")
        # A caller cannot smuggle a modified ToolSpec with the same name past
        # the profile registry.  Equality covers permission, network and
        # side-effect metadata as well as the visible name.
        if tool != registered:
            return SandboxDecision(False, "tool metadata differs from the selected profile", selected_mode, "profile")
        identity = f"{tool.name}:{tool.permission}".lower()
        if any(token in identity for token in self._IMMUTABLE_DENY) or tool.side_effect != "read":
            return SandboxDecision(False, "side-effecting tool is not available in AlphaStock harness", selected_mode, "immutable")
        if tool.permission not in granted:
            return SandboxDecision(False, "required capability was not granted", selected_mode, "capability")
        if tool.network and os.getenv("ALPHASTOCK_SANDBOX_NETWORK", "profile").strip().lower() == "deny":
            return SandboxDecision(False, "network tools are disabled by sandbox policy", selected_mode, "network")

        permission_mode = PermissionMode.BYPASS if selected_mode == "full_access" else PermissionMode.AUTO
        result = evaluate_permission(
            SecurityOperation(
                tool=tool.permission,
                target=tool.name,
                arguments=arguments or {},
                actor_id=actor_id,
                session_id=session_id,
                project_root=self.root,
            ),
            mode=permission_mode,
        )
        return SandboxDecision(
            result.decision.value == "allow",
            result.reason,
            selected_mode,
            result.stage,
        )

    def check_path(self, path: str | Path, *, write: bool = False) -> SandboxDecision:
        operation = SecurityOperation(
            tool="write" if write else "read",
            target=str(path),
            project_root=self.root,
        )
        result = evaluate_permission(operation, mode=PermissionMode.AUTO)
        return SandboxDecision(result.decision.value == "allow", result.reason, self.mode, result.stage)

    def check_command(self, command: str) -> SandboxDecision:
        # There is deliberately no raw-command escape hatch in either existing
        # investment profile.  A future coding profile must supply OS/container
        # isolation before this can become executable.
        return SandboxDecision(False, "raw command execution is disabled for this product", self.mode, "immutable")


# Sandbox is the concise call-site name; PermissionManager is the architecture
# component exposed to the rest of the application.
Sandbox = PermissionManager
