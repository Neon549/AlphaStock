"""Explicit Python control flow equivalent to the fixed investment graph.

Handlers are injected so the state machine is framework-neutral and can be
tested without LangGraph. The existing graph nodes can be adopted gradually.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping


Handler = Callable[[dict[str, Any]], dict[str, Any]]


def run_fixed_workflow(state: dict[str, Any], handlers: Mapping[str, Handler]) -> dict[str, Any]:
    def apply(name: str) -> None:
        state.update(handlers[name](state) or {})

    apply("policy_guard")
    if state.get("publish_status") == "blocked":
        apply("abort")
        return state

    apply("analysts")
    apply("context_snapshot")
    apply("validation")
    if state.get("final_decision"):
        apply("abort")
        return state
    if state.get("replan_required"):
        apply("replan")
        apply("context_snapshot")
        apply("validation")
        if state.get("final_decision"):
            apply("abort")
            return state

    apply("researcher")
    apply("trader")
    apply("output_gate")
    return state
