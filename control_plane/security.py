"""Layered permission checks for agent and server-owned operations.

The project does not expose an arbitrary shell tool to the model.  That is a
security feature, not a missing abstraction.  This module still gives every
future tool one common decision pipeline:

1. static rules (deny always wins),
2. deterministic tool self-checks,
3. an explicit permission mode, and
4. a conservative dynamic fallback for ``auto`` mode.

The last stage is intentionally deterministic for now.  An external model is
not a security boundary and must never be the only thing preventing a
destructive operation.  A classifier can be added behind the same interface
later without changing tool call sites.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import shlex
from dataclasses import dataclass, field
try:
    from enum import StrEnum
except ImportError:  # Python 3.10 compatibility.
    from enum import Enum

    class StrEnum(str, Enum):
        pass
from pathlib import Path
from typing import Any, Iterable, Mapping


LOGGER = logging.getLogger("alphastock.security")
PROJECT_ROOT = Path(
    os.getenv("ALPHASTOCK_PROJECT_ROOT", Path(__file__).resolve().parents[1])
).resolve()


class PermissionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionMode(StrEnum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS = "bypass"
    DONT_ASK = "dontAsk"
    AUTO = "auto"


@dataclass(frozen=True)
class SecurityOperation:
    """A transport-independent description of one side-effecting action."""

    tool: str
    target: str = ""
    arguments: Mapping[str, Any] = field(default_factory=dict)
    actor_id: str | None = None
    session_id: str | None = None
    project_root: Path = PROJECT_ROOT

    @property
    def resource(self) -> str:
        return f"{self.tool}({self.target})" if self.target else self.tool


@dataclass(frozen=True)
class PermissionRule:
    decision: PermissionDecision
    pattern: str

    def matches(self, operation: SecurityOperation) -> bool:
        return fnmatch.fnmatchcase(operation.resource, self.pattern) or fnmatch.fnmatchcase(
            operation.tool, self.pattern
        )


@dataclass(frozen=True)
class PermissionResult:
    decision: PermissionDecision
    stage: str
    reason: str
    bypass_immune: bool = False


_DEFAULT_RULES = {
    "allow": [
        "read(*)",
        "market(*)",
        "document:read",
        "memory:read",
        "market:read",
        "backtest:run",
        "list(*)",
        "agent(*)",
        "backtest(*)",
        "upload(document)",
        "alpha(*)",
    ],
    "deny": [
        "shell(*)",
        "bash(*)",
        "write(.git/*)",
        "edit(.git/*)",
        "write(.claude/*)",
        "edit(.claude/*)",
        "write(.env*)",
        "edit(.env*)",
        "publish(*)",
        "trade(*)",
    ],
    "ask": [],
}


def _load_rules() -> list[PermissionRule]:
    raw: dict[str, Any] = dict(_DEFAULT_RULES)
    policy_path = os.getenv("ALPHASTOCK_PERMISSION_POLICY", "").strip()
    if not policy_path:
        policy_path = str(PROJECT_ROOT / "config" / "security_permissions.json")
    if policy_path:
        try:
            loaded = json.loads(Path(policy_path).read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw.update({key: value for key, value in loaded.items() if key in raw})
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("permission policy ignored: %s", exc)

    rules: list[PermissionRule] = []
    # Deny is evaluated first regardless of configuration order.
    for decision in (PermissionDecision.DENY, PermissionDecision.ALLOW, PermissionDecision.ASK):
        for pattern in raw.get(decision.value, []):
            if isinstance(pattern, str) and pattern.strip():
                rules.append(PermissionRule(decision, pattern.strip()))
    return rules


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


_SENSITIVE_PATH = re.compile(
    r"(^|[\\/])(?:\.git|\.claude)(?:[\\/]|$)|"
    r"(^|[\\/])\.env(?:\.[^\\/]*)?$|"
    r"(?:credential|secret|password|token|id_rsa)",
    re.IGNORECASE,
)
_DANGEROUS_COMMAND = re.compile(
    r"(?:\brm\s+-rf\b|\bdel\s+/[sqf]+\b|\bformat\b|\bshutdown\b|"
    r"\bgit\s+(?:push|reset\s+--hard|clean\s+-fd)\b|"
    r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sh|bash|pwsh|powershell)\b|"
    r"\b(?:chmod\s+777|sudo)\b|\b(?:powershell|pwsh)\s+.*\-enc(?:odedcommand)?\b)",
    re.IGNORECASE,
)
_NO_SIDE_EFFECT_TOOLS = {"read", "grep", "glob", "list", "sleep", "todowrite", "market"}
_WRITE_TOOLS = {"write", "edit", "upload", "delete", "publish", "trade"}


def _tool_self_check(operation: SecurityOperation) -> PermissionResult | None:
    """Make content-aware checks that a coarse rule cannot express."""

    tool = operation.tool.lower()
    target = str(operation.target or "")
    command = str(operation.arguments.get("command") or target)

    if tool in {"shell", "bash", "command", "exec"}:
        if _DANGEROUS_COMMAND.search(command) or any(
            marker in command for marker in ("\x00", "\n", "\r", ";", "&&", "||", "|", "`", "$(", ">", "<")
        ):
            return PermissionResult(
                PermissionDecision.DENY,
                "tool_self_check",
                "dangerous shell syntax or destructive command",
                bypass_immune=True,
            )
        # Parsing is deliberately conservative.  A future shell tool must
        # provide an AST-backed checker before this branch can allow writes.
        try:
            shlex.split(command, posix=False)
        except ValueError:
            return PermissionResult(PermissionDecision.DENY, "tool_self_check", "shell parse failed", True)
        return None

    if tool in {"write", "edit", "delete", "upload"}:
        path = Path(target)
        if not path.is_absolute():
            path = operation.project_root / path
        if not _within(path, operation.project_root):
            return PermissionResult(PermissionDecision.DENY, "tool_self_check", "path escapes project root", True)
        if _SENSITIVE_PATH.search(path.as_posix()):
            return PermissionResult(PermissionDecision.DENY, "tool_self_check", "sensitive path is bypass-immune", True)
        return PermissionResult(PermissionDecision.ALLOW, "tool_self_check", "project-scoped file operation")

    if tool in {"publish", "trade"}:
        return PermissionResult(PermissionDecision.DENY, "tool_self_check", "high-impact operation requires human review", True)

    return None


def _dynamic_fallback(operation: SecurityOperation) -> PermissionResult:
    """Conservative two-stage fallback used by ``auto`` mode.

    Stage 1 is a cheap safe-tool check.  Stage 2 is a stricter deterministic
    classifier.  Unknown operations fail closed; this is the correct behavior
    for unattended agent execution.
    """

    tool = operation.tool.lower()
    if tool in _NO_SIDE_EFFECT_TOOLS:
        return PermissionResult(PermissionDecision.ALLOW, "dynamic_stage_1", "zero-side-effect tool")

    target = str(operation.target or "")
    if tool in _WRITE_TOOLS:
        path = Path(target)
        if not path.is_absolute():
            path = operation.project_root / path
        if _within(path, operation.project_root) and not _SENSITIVE_PATH.search(path.as_posix()):
            return PermissionResult(PermissionDecision.ALLOW, "dynamic_stage_1", "project-scoped edit")

    if tool in {"shell", "bash", "command", "exec"} and not _DANGEROUS_COMMAND.search(target):
        # Shell remains blocked in auto mode until an explicit allow rule is
        # configured.  This prevents a lexical classifier from becoming a
        # privilege escalation surface.
        return PermissionResult(PermissionDecision.DENY, "dynamic_stage_2", "shell requires explicit allow rule")

    return PermissionResult(PermissionDecision.DENY, "dynamic_stage_2", "operation is not confidently safe")


def evaluate_permission(
    operation: SecurityOperation,
    *,
    mode: PermissionMode | str | None = None,
    rules: Iterable[PermissionRule] | None = None,
) -> PermissionResult:
    """Evaluate one operation through the four-layer permission pipeline."""

    try:
        selected_mode = PermissionMode(mode or os.getenv("AGENT_PERMISSION_MODE", "default"))
    except ValueError:
        selected_mode = PermissionMode.DEFAULT
    configured_rules = list(rules) if rules is not None else _load_rules()

    deny = next((rule for rule in configured_rules if rule.decision == PermissionDecision.DENY and rule.matches(operation)), None)
    if deny:
        return PermissionResult(PermissionDecision.DENY, "rules", f"deny rule matched: {deny.pattern}", True)

    self_check = _tool_self_check(operation)
    if self_check and self_check.decision == PermissionDecision.DENY:
        return self_check

    allow = next((rule for rule in configured_rules if rule.decision == PermissionDecision.ALLOW and rule.matches(operation)), None)
    if allow:
        return PermissionResult(PermissionDecision.ALLOW, "rules", f"allow rule matched: {allow.pattern}")
    if self_check and self_check.decision == PermissionDecision.ALLOW:
        if selected_mode in {PermissionMode.PLAN, PermissionMode.DONT_ASK}:
            return PermissionResult(PermissionDecision.DENY, "mode", f"{selected_mode.value} blocks this operation")
        return self_check

    if selected_mode == PermissionMode.BYPASS:
        return PermissionResult(PermissionDecision.ALLOW, "mode", "bypass mode after immutable checks")
    if selected_mode == PermissionMode.DONT_ASK:
        return PermissionResult(PermissionDecision.DENY, "mode", "unmatched operation in dontAsk mode")
    if selected_mode == PermissionMode.PLAN and operation.tool.lower() in _WRITE_TOOLS:
        return PermissionResult(PermissionDecision.DENY, "mode", "plan mode is read-only")
    if selected_mode == PermissionMode.ACCEPT_EDITS and operation.tool.lower() in {"write", "edit"}:
        return PermissionResult(PermissionDecision.ALLOW, "mode", "project edit in acceptEdits mode")
    if selected_mode == PermissionMode.AUTO:
        return _dynamic_fallback(operation)
    return PermissionResult(PermissionDecision.ASK, "mode", "explicit user approval is required")


def authorize_operation(
    operation: SecurityOperation,
    *,
    mode: PermissionMode | str | None = None,
    rules: Iterable[PermissionRule] | None = None,
) -> PermissionResult:
    """Return a decision and fail closed for non-interactive agent callers."""

    result = evaluate_permission(operation, mode=mode, rules=rules)
    LOGGER.info(
        "security_decision tool=%s target=%s decision=%s stage=%s actor=%s session=%s reason=%s",
        operation.tool,
        operation.target[:120],
        result.decision.value,
        result.stage,
        operation.actor_id or "anonymous",
        operation.session_id or "none",
        result.reason,
    )
    if result.decision != PermissionDecision.ALLOW:
        raise PermissionError(f"operation {operation.resource} is not authorized: {result.reason}")
    return result
