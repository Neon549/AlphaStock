"""Compile validated intent tasks into a small, auditable execution DAG.

This module is intentionally model-free.  The parser may use an LLM to obtain
candidate slots, but graph validation, dependency ordering, capability binding
and confirmation boundaries are deterministic.
"""

from __future__ import annotations

import concurrent.futures
from typing import Any


class TaskGraphError(ValueError):
    """Raised when a task plan cannot be safely compiled."""


_TASK_SKILLS: dict[str, tuple[str, ...]] = {
    "investment_analysis": ("analysis",),
    "backtest": ("backtest",),
    # The current chat harness has no direct scan/screening tool binding.  The
    # plan remains visible and the API can route it to its dedicated endpoint.
    "market_scan": (),
    "strategy_screen": (),
    "system_action": (),
    "discussion": (),
    "clarify": (),
    # Trading is deliberately unbound: no model may turn a request into an
    # order, even after a user confirms it, until a separately approved broker
    # integration exists.
    "trade_action": (),
}
_STOCK_REQUIRED = {"investment_analysis", "backtest"}


def _as_string_or_none(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _normalise_task(raw: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TaskGraphError("each sub_intent must be an object")
    intent = _as_string_or_none(raw.get("intent"))
    if intent not in _TASK_SKILLS:
        raise TaskGraphError(f"unsupported sub_intent: {intent!r}")
    task_id = _as_string_or_none(raw.get("task_id")) or f"{intent}-{index + 1}"
    raw_dependencies = raw.get("depends_on") or []
    if not isinstance(raw_dependencies, list) or not all(isinstance(item, str) for item in raw_dependencies):
        raise TaskGraphError(f"depends_on for {task_id} must be a string list")
    depends_on = list(dict.fromkeys(item.strip() for item in raw_dependencies if item.strip()))
    if task_id in depends_on:
        raise TaskGraphError(f"task {task_id} cannot depend on itself")

    raw_slots = raw.get("slots") or {}
    if not isinstance(raw_slots, dict):
        raise TaskGraphError(f"slots for {task_id} must be an object")
    slots = {
        key: value for key, value in raw_slots.items()
        if key in {"stock_code", "stock_name", "analyst_focus", "strategy"}
        and (value is None or isinstance(value, (str, int, float, bool)))
    }
    missing_slots = list(dict.fromkeys(
        str(item) for item in (raw.get("missing_slots") or [])
        if str(item) in {"stock_code", "strategy"}
    ))
    if intent in _STOCK_REQUIRED and not _as_string_or_none(slots.get("stock_code")):
        if "stock_code" not in missing_slots:
            missing_slots.append("stock_code")

    requires_confirmation = bool(raw.get("requires_confirmation", False)) or intent == "trade_action"
    return {
        "task_id": task_id,
        "intent": intent,
        "depends_on": depends_on,
        "slots": slots,
        "missing_slots": missing_slots,
        "requires_confirmation": requires_confirmation,
        "risk_level": "high" if requires_confirmation else "read_only",
        "skill_binding": list(_TASK_SKILLS[intent]),
    }


def _topological_stages(tasks: list[dict[str, Any]]) -> list[list[str]]:
    by_id = {task["task_id"]: task for task in tasks}
    if len(by_id) != len(tasks):
        raise TaskGraphError("task_id values must be unique")
    for task in tasks:
        unknown = [dependency for dependency in task["depends_on"] if dependency not in by_id]
        if unknown:
            raise TaskGraphError(f"task {task['task_id']} has unknown dependencies: {unknown}")

    remaining = {task["task_id"]: set(task["depends_on"]) for task in tasks}
    stages: list[list[str]] = []
    while remaining:
        ready = sorted(task_id for task_id, dependencies in remaining.items() if not dependencies)
        if not ready:
            raise TaskGraphError("task dependencies contain a cycle")
        stages.append(ready)
        for task_id in ready:
            remaining.pop(task_id)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)
    return stages


def _runnable_stages(tasks: list[dict[str, Any]], stages: list[list[str]]) -> list[list[str]]:
    by_id = {task["task_id"]: task for task in tasks}
    completed_or_blocked: dict[str, str] = {}
    result: list[list[str]] = []
    for stage in stages:
        runnable: list[str] = []
        for task_id in stage:
            task = by_id[task_id]
            dependencies = task["depends_on"]
            dependency_blocked = any(completed_or_blocked.get(item) != "runnable" for item in dependencies)
            if dependency_blocked:
                task["planned_status"] = "blocked_dependency"
            elif task["requires_confirmation"]:
                task["planned_status"] = "awaiting_confirmation"
            elif task["missing_slots"]:
                task["planned_status"] = "blocked_missing_slots"
            elif not task["skill_binding"] and task["intent"] not in {"discussion", "clarify"}:
                task["planned_status"] = "route_to_dedicated_endpoint"
            else:
                task["planned_status"] = "runnable"
                runnable.append(task_id)
            completed_or_blocked[task_id] = task["planned_status"]
        if runnable:
            result.append(runnable)
    return result


def build_task_dag(sub_intents: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Return a validated DAG and execution boundaries for ``sub_intents``.

    ``stages`` is the topological representation of the requested work: task
    IDs in the same stage are independent and may run in parallel.  The
    narrower ``runnable_stages`` excludes tasks blocked by missing slots,
    confirmation, or an unavailable local skill binding.
    """
    if sub_intents is None:
        sub_intents = []
    if not isinstance(sub_intents, list):
        raise TaskGraphError("sub_intents must be a list")
    tasks = [_normalise_task(item, index) for index, item in enumerate(sub_intents)]
    stages = _topological_stages(tasks) if tasks else []
    runnable = _runnable_stages(tasks, stages)
    edges = [
        {"from": dependency, "to": task["task_id"]}
        for task in tasks
        for dependency in task["depends_on"]
    ]
    return {
        "schema_version": "task-dag/v1",
        "tasks": tasks,
        "edges": edges,
        "stages": stages,
        "runnable_stages": runnable,
        "multi_intent": len(tasks) > 1,
        "confirmation_required": any(task["requires_confirmation"] for task in tasks),
        "has_blocked_tasks": any(task["planned_status"] != "runnable" for task in tasks),
    }


def execute_task_dag(
    task_plan: dict[str, Any],
    task_executor,
    *,
    max_workers: int = 3,
) -> dict[str, Any]:
    """Execute independent, pre-authorized tasks stage-by-stage.

    The caller supplies the capability-bound ``task_executor``; this function
    never looks up a tool or broker itself.  Tasks in one topological stage are
    fanned out concurrently, while a failed/blocked dependency prevents every
    downstream task from starting.  Confirmation tasks are represented in the
    result but never handed to the executor.
    """
    tasks = task_plan.get("tasks") if isinstance(task_plan, dict) else None
    if not isinstance(tasks, list):
        raise TaskGraphError("task_plan must contain a task list")
    # Recompile raw tasks so callers cannot bypass validation by forging a plan.
    compiled = build_task_dag(tasks)
    by_id = {task["task_id"]: task for task in compiled["tasks"]}
    status: dict[str, str] = {task_id: "pending" for task_id in by_id}
    results: dict[str, dict[str, Any]] = {}

    for stage in compiled["stages"]:
        runnable: list[dict[str, Any]] = []
        for task_id in stage:
            task = by_id[task_id]
            if any(status[dependency] != "succeeded" for dependency in task["depends_on"]):
                status[task_id] = "blocked_dependency"
                continue
            if task["requires_confirmation"]:
                status[task_id] = "awaiting_confirmation"
                continue
            if task["missing_slots"]:
                status[task_id] = "blocked_missing_slots"
                continue
            if not task["skill_binding"] and task["intent"] not in {"discussion", "clarify"}:
                status[task_id] = "route_to_dedicated_endpoint"
                continue
            runnable.append(task)

        if not runnable:
            continue
        worker_count = min(max_workers, len(runnable))
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {pool.submit(task_executor, task): task for task in runnable}
            for future in concurrent.futures.as_completed(futures):
                task = futures[future]
                task_id = task["task_id"]
                try:
                    output = future.result()
                    if not isinstance(output, dict):
                        output = {"ok": bool(output), "result": output}
                    ok = bool(output.get("ok", True))
                    status[task_id] = "succeeded" if ok else "failed"
                    results[task_id] = {"status": status[task_id], "output": output}
                except Exception as exc:
                    status[task_id] = "failed"
                    results[task_id] = {"status": "failed", "error": str(exc)[:300]}

    for task_id, task in by_id.items():
        if status[task_id] != "pending":
            continue
        # This is possible only when an earlier stage was non-runnable.
        status[task_id] = "blocked_dependency" if task["depends_on"] else "blocked"
    return {
        "task_plan": compiled,
        "task_status": status,
        "task_results": results,
        "all_runnable_tasks_succeeded": all(
            status[task["task_id"]] == "succeeded"
            for task in compiled["tasks"]
            if task["planned_status"] == "runnable"
        ),
    }
