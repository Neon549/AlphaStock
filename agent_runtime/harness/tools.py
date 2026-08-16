"""Unified, checkpointed tool dispatch for all Harness profiles."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Callable

from agent_runtime.harness.profiles import ToolSpec
from agent_runtime.reliability import classify_tool_failure, invoke_with_failure_policy

if TYPE_CHECKING:  # pragma: no cover
    from agent_runtime.harness.run import RunHandle


def _safe_observation(tool: str, result: dict[str, Any], result_ref: str, *, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool": tool,
        "ok": bool(result.get("ok")),
        "result_ref": result_ref,
        "source_kind": str(result.get("source_kind") or "evidence"),
        "citations": list(result.get("citations") or []),
        "freshness": dict(result.get("freshness") or {}),
        "tool_metadata": dict(result.get("tool_metadata") or {}),
        "tool_failure": result.get("tool_failure"),
        "degraded": bool(result.get("degraded")),
        "arguments_sha256": hashlib.sha256(
            json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
    }


class ToolGateway:
    def call(
        self,
        run: "RunHandle",
        *,
        tool: ToolSpec,
        granted: set[str],
        arguments: dict[str, Any],
        cache_key: str,
        invoke: Callable[[], dict[str, Any]],
        stock_code: str | None = None,
    ) -> dict[str, Any]:
        decision = run.sandbox.check(
            run.profile,
            tool,
            granted=granted,
            arguments=arguments,
            session_id=run.state.data.get("session_id"),
            actor_id=run.state.data.get("actor_id"),
            approval_mode=run.state.data.get("approval_mode"),
        )
        if not decision.allowed:
            failure = classify_tool_failure(PermissionError(decision.reason))
            result: dict[str, Any] = {
                "ok": False,
                "content": f"[TOOL_ERROR] error_type={failure.error_type.value} message={failure.message}",
                "tool_failure": failure.to_dict(),
                "attempts": 0,
                "retry_trace": [],
                "circuit_state": "closed",
            }
            run.record("sandbox_denied", tool=tool.name, stage=decision.stage, reason=decision.reason)
        else:
            run.record("tool_started", tool=tool.name)
            result = invoke_with_failure_policy(
                tool.name,
                invoke,
                cache_key=cache_key,
                retry_budget=run.retry_budget,
            )

        source_kind = str(result.get("source_kind") or "evidence")
        result_ref = run.evidence.put(
            tool=tool.name,
            content=str(result.get("content") or ""),
            source_kind=source_kind,
            citations=list(result.get("citations") or []),
            stock_code=stock_code,
        )
        result["result_ref"] = result_ref
        run.state.step += 1
        run.state.data["retry_budget"] = run.retry_budget.summary()
        observations = run.state.data.setdefault("observations", [])
        observations.append(_safe_observation(tool.name, result, result_ref, arguments=arguments))
        run.record(
            "tool_finished",
            tool=tool.name,
            ok=bool(result.get("ok")),
            result_ref=result_ref,
            attempts=result.get("attempts", 1),
            sandbox_mode=decision.mode,
        )
        run.checkpoint("tool_finished")
        return result
