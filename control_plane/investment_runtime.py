"""Investment-specific runtime behind the generic Gateway.

The default workflow is a framework-neutral Python state machine. LangGraph is
kept as an opt-in compatibility adapter for regression comparison and rollback;
FastAPI, CLI, cron and webhook callers use the same event contract either way.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable
from uuid import uuid4

from control_plane.contracts import AgentEvent, AgentRunResult
from agent_runtime.context.window import ContextWindowBuilder
from agent_runtime.context.budget import ContextBudgetExceeded
from agent_runtime.memory.manager import MemoryManager, NullMemoryManager


class InvestmentRuntime:
    """Route one investment event to a safe direct reply or a bounded workflow."""

    def __init__(
        self,
        *,
        intent_parser: Callable[[str], dict[str, Any]] | None = None,
        workflow_runner: Callable[..., dict[str, Any]] | None = None,
        discussion_runner: Callable[[str], str] | None = None,
        skill_selector: Callable[..., list[Any]] | None = None,
        skill_executor: Callable[..., Any] | None = None,
        memory_manager: MemoryManager | None = None,
        context_builder: ContextWindowBuilder | None = None,
        workflow_runtime: str | None = None,
        execution_mode: str | None = None,
        agent_loop_runner: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ):
        self._intent_parser = intent_parser
        self._workflow_runner = workflow_runner
        self._discussion_runner = discussion_runner
        self._skill_selector = skill_selector
        self._skill_executor = skill_executor
        self._memory = memory_manager or NullMemoryManager()
        self._context_builder = context_builder or ContextWindowBuilder()
        configured_runtime = workflow_runtime or os.getenv("INVESTMENT_WORKFLOW_RUNTIME", "python")
        if configured_runtime not in {"langgraph", "python"}:
            raise ValueError("workflow_runtime must be 'langgraph' or 'python'")
        self._workflow_runtime = configured_runtime
        configured_mode = execution_mode or os.getenv("INVESTMENT_EXECUTION_MODE", "agent_loop")
        if configured_mode not in {"agent_loop", "workflow"}:
            raise ValueError("execution_mode must be 'agent_loop' or 'workflow'")
        self._execution_mode = configured_mode
        self._agent_loop_runner = agent_loop_runner

    def _deps(self) -> None:
        """Load only routing dependencies; model/workflow imports stay branch-local."""
        if self._intent_parser is None:
            from api.intent_parser import parse_intent
            self._intent_parser = parse_intent
        
    def _discussion(self, query: str, runtime_context: str, model_profile: str) -> str:
        injected_runner = self._discussion_runner is not None
        if self._discussion_runner is None:
            import config.llm_config as llm_cfg

            def discuss(text: str) -> str:
                prompt = (
                    "You are an A-share research assistant. Answer in a professional, "
                    "plain style. This is an analytical view, not investment advice. "
                    "The supplied runtime context includes non-evidence preferences and "
                    "session history, which must not become factual claims.\n\n"
                    f"Runtime context:\n{runtime_context}\n\n"
                    f"User: {text}"
                )
                return llm_cfg.quick_llm.invoke(prompt).content

            self._discussion_runner = discuss
        if injected_runner:
            return self._discussion_runner(query)
        from control_plane.model_profile import model_scope
        with model_scope(model_profile):
            return self._discussion_runner(query)

    def _workflow(self) -> Callable[..., dict[str, Any]]:
        if self._workflow_runner is None:
            if self._workflow_runtime == "python":
                from agent_runtime.workflows.runtime import PythonInvestmentRuntime

                self._workflow_runner = PythonInvestmentRuntime().run
            else:
                from agent_runtime.workflows.runtime import LangGraphInvestmentRuntime

                self._workflow_runner = LangGraphInvestmentRuntime().run
        return self._workflow_runner

    def _agent_loop(self) -> Callable[[dict[str, Any]], dict[str, Any]]:
        if self._agent_loop_runner is None:
            from agent_runtime.agents.investment_harness import run_investment_agent_loop

            self._agent_loop_runner = run_investment_agent_loop
        return self._agent_loop_runner

    def _skills(self) -> tuple[Callable[..., list[Any]], Callable[..., Any]]:
        if self._skill_selector is None or self._skill_executor is None:
            from agent_runtime.skills.registry import skill_registry
            self._skill_selector = skill_registry.select
            self._skill_executor = skill_registry.execute
        return self._skill_selector, self._skill_executor

    @staticmethod
    def _direct_reply(run_id: str, route: str, content: str, intent: int) -> AgentRunResult:
        return AgentRunResult(
            run_id=run_id,
            route=route,
            payload={"role": "assistant", "content": content, "intent": intent},
            trace=[{"event": "route_selected", "route": route, "intent": intent}],
        )

    def _retrieve_document_context(
        self, event: AgentEvent, parsed: dict[str, Any]
    ) -> tuple[str, list[dict], list[str], list[str]]:
        """Skills remain permission-gated even though workflow selection is model-assisted."""
        skill_selector, skill_executor = self._skills()
        selected = skill_selector(
            event.content,
            context={"has_session_document": bool(event.session_id), "intent": parsed["intent"]},
            granted_permissions={"document:read", "market:read", "backtest:run"},
            llm=None,  # deterministic trigger here; ResearchHarness remains the dynamic tool loop.
        )
        versions = [skill.version_id for skill in selected]
        summaries = [f"{skill.name} ({skill.version}): {skill.description}" for skill in selected]
        if not any(skill.name == "document-rag" for skill in selected):
            return "", [], versions, summaries
        result = skill_executor(
            "document-rag",
            granted_permissions={"document:read", "market:read", "backtest:run"},
            session_id=event.session_id,
            query=event.content,
        )
        return result.get("context", ""), result.get("citations", []), versions, summaries

    def _select_skill_summaries(self, event: AgentEvent, parsed: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Expose the allowed Skill catalog to the planner without pre-running a Skill."""
        skill_selector, _ = self._skills()
        selected = skill_selector(
            event.content,
            context={"has_session_document": bool(event.session_id), "intent": parsed["intent"]},
            granted_permissions={"document:read", "market:read", "backtest:run", "memory:read"},
            llm=None,
        )
        return (
            [skill.version_id for skill in selected],
            [f"{skill.name} ({skill.version}): {skill.description}" for skill in selected],
        )

    def run(self, event: AgentEvent) -> AgentRunResult:
        self._deps()
        run_id = str(uuid4())
        query = event.content.strip()
        if not query:
            return self._direct_reply(run_id, "clarify", "请输入需要分析的股票或问题。", 4)

        parsed = self._intent_parser(query)
        intent = int(parsed["intent"])
        model_profile = str(event.metadata.get("model", "smart"))
        memory_context = self._memory.load_context(event, parsed.get("stock_code"))
        base_trace = [
            {"event": "intent_parsed", "intent": intent, "event_id": event.event_id},
            {
                "event": "memory_context_loaded",
                "has_session_memory": bool(memory_context.get("session")),
                "has_preferences": bool(memory_context.get("preferences")),
            },
        ]

        if intent == 4:
            result = self._direct_reply(run_id, "clarify", parsed.get("reply_hint") or "请补充股票名称或代码。", intent)
            result.trace = base_trace + result.trace
            return result
        if intent == 1:
            try:
                window = self._context_builder.build(
                    profile="discussion", user_message=query, memory_context=memory_context
                )
            except ContextBudgetExceeded:
                result = self._direct_reply(
                    run_id, "clarify", "当前消息过长，请拆分问题或将材料作为文档上传后再检索。", 4
                )
                result.trace = base_trace + [{"event": "context_budget_blocked", "profile": "discussion"}]
                return result
            result = self._direct_reply(run_id, "discussion", self._discussion(query, window.text, model_profile), intent)
            result.trace = base_trace + result.trace
            self._memory.remember_run(event, parsed, None, result.payload["content"], run_id)
            return result
        if intent == 3:
            result = self._direct_reply(run_id, "system_action", "请通过回测、扫描或筛选入口提交对应参数。", intent)
            result.trace = base_trace + result.trace
            return result

        stock_code = parsed.get("stock_code")
        if not stock_code:
            result = self._direct_reply(run_id, "clarify", "请补充可确认的 A 股名称或六位代码。", 4)
            result.trace = base_trace + result.trace
            return result
        # An injected workflow runner is used by unit tests and by callers that
        # explicitly opt into the fixed compatibility workflow. Production uses
        # the agent loop by default, so document retrieval remains a planner
        # decision instead of an unconditional pre-step.
        use_fixed_workflow = self._workflow_runner is not None or self._execution_mode == "workflow"
        if use_fixed_workflow:
            doc_context, citations, skill_versions, skill_summaries = self._retrieve_document_context(event, parsed)
        else:
            skill_versions, skill_summaries = self._select_skill_summaries(event, parsed)
            doc_context, citations = "", []
        try:
            window = self._context_builder.build(
                profile="research",
                user_message=query,
                memory_context=memory_context,
                selected_skill_summaries=skill_summaries,
            )
        except ContextBudgetExceeded:
            result = self._direct_reply(
                run_id, "clarify", "当前消息过长，请拆分问题或将材料作为文档上传后再检索。", 4
            )
            result.trace = base_trace + [{"event": "context_budget_blocked", "profile": "research"}]
            return result
        if use_fixed_workflow:
            result = self._workflow()(
                stock_code,
                doc_context=doc_context,
                document_citations=citations,
                session_id=event.session_id,
                analysis_query=query,
                analyst_focus=parsed.get("analyst_focus") or "all",
                memory_context=memory_context,
                agent_context=window.text,
                model_profile=model_profile,
            )
            route = "investment_workflow"
        else:
            from control_plane.model_profile import model_scope

            with model_scope(model_profile):
                result = self._agent_loop()({
                    "stock_code": stock_code,
                    "user_doc_context": doc_context,
                    "document_citations": citations,
                    "session_id": event.session_id,
                    "analysis_query": query,
                    "analyst_focus": parsed.get("analyst_focus") or "all",
                    "memory_context": memory_context,
                    "agent_context": window.text,
                    "model_profile": model_profile,
                })
            route = "investment_agent_loop"
        payload = {
            "role": "assistant",
            "intent": intent,
            "stock_code": stock_code,
            "stock_name": parsed.get("stock_name"),
            "analyst_focus": parsed.get("analyst_focus") or "all",
            "decision": result.get("draft_decision") or result.get("final_decision", ""),
            "fundamental_report": result.get("fundamental_report", ""),
            "technical_report": result.get("technical_report", ""),
            "sentiment_report": result.get("sentiment_report", ""),
            "researcher_analysis": result.get("bull_argument", ""),
            "status": result.get("publish_status", "success"),
            "publish_status": result.get("publish_status", "blocked"),
            "publish_reasons": result.get("publish_reasons", []),
            "human_review_required": result.get("human_review_required", False),
            "document_citations": result.get("document_citations", citations),
            "evidence_cards": result.get("evidence_cards", []),
            "selected_skills": skill_versions,
            "workflow_result": result,
        }
        self._memory.remember_run(event, parsed, result, payload["decision"], run_id)
        return AgentRunResult(
            run_id=run_id,
            route=route,
            payload=payload,
            trace=base_trace + [
                {"event": "route_selected", "route": route, "stock_code": stock_code},
                {"event": "skills_selected", "versions": skill_versions},
                {
                    "event": "context_window_built",
                    "profile": window.profile,
                    "estimated_tokens": window.estimated_tokens,
                    "mode": window.mode,
                    "soft_limit": window.soft_limit,
                    "hard_limit": window.hard_limit,
                    "omitted_blocks": window.omitted_blocks,
                },
            ] + [
                {"event": "research_harness", "detail": step}
                for step in result.get("agent_trace", [])
            ],
        )
