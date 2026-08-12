"""Bounded application services exposed through the remote MCP adapter.

This module has no dependency on the MCP SDK. Keeping authentication/transport
separate lets the same safe operations be unit tested and, later, reused by a
different remote adapter without copying investment-domain rules.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable

from api.document_processing.retrieval import extract_document_citations, retrieve_document_context
from api.security import claim_session
from control_plane.contracts import AgentEvent, TriggerType
from control_plane.gateway import Gateway
from control_plane.security import SecurityOperation, authorize_operation


_STOCK_CODE = re.compile(r"^\d{6}$")
_MAX_QUERY_CHARS = 1_000
_MAX_DOCUMENT_RESULTS = 5

SCOPE_RESEARCH = "research:read"
SCOPE_BACKTEST = "backtest:run"
SCOPE_KNOWLEDGE = "knowledge:read"
SCOPE_DOCUMENT = "document:read"
READ_ONLY_SCOPES = frozenset({SCOPE_RESEARCH, SCOPE_BACKTEST, SCOPE_KNOWLEDGE, SCOPE_DOCUMENT})


class MCPInputError(ValueError):
    """A caller supplied an invalid or over-broad tool argument."""


class MCPAuthorizationError(PermissionError):
    """The authenticated MCP principal lacks a required capability."""


@dataclass(frozen=True)
class MCPPrincipal:
    """Transport-neutral authenticated remote caller."""

    actor_id: str
    scopes: frozenset[str]
    is_service_account: bool = False


def _bounded_text(value: str, *, field: str, required: bool = True) -> str:
    text = (value or "").strip()
    if required and not text:
        raise MCPInputError(f"{field} is required")
    if len(text) > _MAX_QUERY_CHARS:
        raise MCPInputError(f"{field} exceeds {_MAX_QUERY_CHARS} characters")
    return text


def _stock_code(value: str) -> str:
    code = (value or "").strip()
    if not _STOCK_CODE.fullmatch(code):
        raise MCPInputError("stock_code must be a six-digit A-share code")
    return code


def _bounded_top_k(value: int, *, maximum: int) -> int:
    try:
        requested = int(value)
    except (TypeError, ValueError) as exc:
        raise MCPInputError("top_k must be an integer") from exc
    return max(1, min(requested, maximum))


class MCPToolService:
    """Read-only remote-tool facade over existing project capabilities.

    No method exposes arbitrary SQL, filesystem access, publication approval or
    trade execution. ``research_stock`` returns a governed research draft; it
    cannot create a publication review or publish a conclusion.
    """

    def __init__(self, gateway_factory: Callable[[], Gateway] | None = None):
        self._gateway_factory = gateway_factory or self._default_gateway

    @staticmethod
    def _default_gateway() -> Gateway:
        # Construct lazily: mounting the MCP endpoint must not import models or
        # create a database pool until a client actually invokes a research tool.
        from agent_runtime.memory.manager import PostgresMemoryManager
        from control_plane.investment_runtime import InvestmentRuntime
        from control_plane.run_store import PostgresRunStore

        return Gateway(InvestmentRuntime(memory_manager=PostgresMemoryManager()), store=PostgresRunStore())

    @staticmethod
    def _require(principal: MCPPrincipal, scope: str) -> None:
        if scope not in principal.scopes:
            raise MCPAuthorizationError(f"missing MCP scope: {scope}")

    @staticmethod
    def _authorize(operation: str, target: str, principal: MCPPrincipal, session_id: str | None = None) -> None:
        try:
            authorize_operation(
                SecurityOperation(
                    tool=operation,
                    target=target,
                    actor_id=principal.actor_id,
                    session_id=session_id,
                ),
                mode="auto",
            )
        except PermissionError as exc:
            raise MCPAuthorizationError("operation is not permitted") from exc

    @staticmethod
    def _safe_model_profile(value: str) -> str:
        profile = (value or "smart").strip().lower()
        if profile not in {"smart", "deep"}:
            raise MCPInputError("model_profile must be 'smart' or 'deep'")
        return profile

    def list_capabilities(self, principal: MCPPrincipal) -> dict[str, Any]:
        """Return only Registry-approved skill metadata, never their internals."""

        self._require(principal, SCOPE_KNOWLEDGE)
        from agent_runtime.skills.registry import skill_registry

        return {
            "capabilities": skill_registry.list_public(),
            "remote_policy": {
                "read_only": True,
                "publication": "not available through MCP",
                "trade_execution": "not available through MCP",
                "database_access": "not available through MCP",
            },
        }

    def research_stock(
        self,
        principal: MCPPrincipal,
        *,
        stock_code: str,
        question: str = "",
        focus: str = "all",
        session_id: str | None = None,
        model_profile: str = "smart",
    ) -> dict[str, Any]:
        """Run the governed runtime and return a draft that always requires review."""

        self._require(principal, SCOPE_RESEARCH)
        code = _stock_code(stock_code)
        requested_focus = (focus or "all").strip().lower()
        if requested_focus not in {"all", "technical", "fundamental", "sentiment"}:
            raise MCPInputError("focus must be all, technical, fundamental or sentiment")
        profile = self._safe_model_profile(model_profile)
        if principal.is_service_account and profile != "smart":
            raise MCPAuthorizationError("service accounts may use only the smart model profile")
        query = _bounded_text(question, field="question", required=False) or f"分析 {code}"
        claimed_session: str | None = None
        if session_id:
            self._require(principal, SCOPE_DOCUMENT)
            if principal.is_service_account:
                # A shared integration secret must never inherit an individual
                # user's uploaded document scope.
                raise MCPAuthorizationError("service accounts cannot access session documents")
            claimed_session = claim_session(session_id, principal.actor_id)

        self._authorize("agent", "mcp_research", principal, claimed_session)
        run = self._gateway_factory().dispatch(
            AgentEvent(
                trigger=TriggerType.MCP,
                content=query,
                session_id=claimed_session,
                actor_id=principal.actor_id,
                channel="mcp",
                metadata={
                    "operation": "mcp_research_stock",
                    "model": profile,
                    "requested_focus": requested_focus,
                },
            )
        )
        payload = dict(run.payload)
        publish_status = payload.get("publish_status") or "requires_human_review"
        # MCP never becomes a release channel, even if a future runtime returns
        # a more permissive status by mistake.
        if publish_status not in {"blocked", "requires_human_review"}:
            publish_status = "requires_human_review"
        return {
            "run_id": run.run_id,
            "route": run.route,
            "stock_code": code,
            "analyst_focus": requested_focus,
            "decision_draft": payload.get("decision", ""),
            "technical_report": payload.get("technical_report", ""),
            "fundamental_report": payload.get("fundamental_report", ""),
            "sentiment_report": payload.get("sentiment_report", ""),
            "researcher_analysis": payload.get("researcher_analysis", ""),
            "document_citations": payload.get("document_citations", []),
            "evidence_cards": payload.get("evidence_cards", []),
            "publish_status": publish_status,
            "human_review_required": True,
            "notice": "Research draft only. It cannot be published or used to execute a trade through MCP.",
        }

    def run_backtest(
        self,
        principal: MCPPrincipal,
        *,
        stock_code: str,
        strategy: str = "kdj_macd",
        start_date: str = "20220101",
        end_date: str = "20261231",
        initial_cash: float = 100000.0,
    ) -> dict[str, Any]:
        """Run one bounded historical backtest without persisting a user decision."""

        self._require(principal, SCOPE_BACKTEST)
        code = _stock_code(stock_code)
        strategy_name = _bounded_text(strategy, field="strategy")
        self._authorize("backtest", strategy_name, principal)
        try:
            cash = float(initial_cash)
            if not math.isfinite(cash):
                raise MCPInputError("initial_cash must be a finite number")
            from backtest.service import BacktestInputError, execute_backtest

            execution = execute_backtest(
                stock_code=code,
                strategy=strategy_name,
                start_date=_bounded_text(start_date, field="start_date"),
                end_date=_bounded_text(end_date, field="end_date"),
                initial_cash=cash,
            )
        except BacktestInputError as exc:
            raise MCPInputError(str(exc)) from exc

        result = execution["result"]
        return {
            "stock_code": execution["stock_code"],
            "strategy": execution["strategy"],
            "data_source": execution["data_source"],
            "total_return": result["total_return"],
            "sharpe": result["sharpe"],
            "max_drawdown": result["max_drawdown"],
            "trade_count": result["trade_count"],
            "win_rate": result["win_rate"],
            "report": execution["report_text"],
            "notice": "Historical backtest only; it does not imply future performance or create a trade instruction.",
        }

    def search_strategy_knowledge(self, principal: MCPPrincipal, *, query: str, top_k: int = 3) -> dict[str, Any]:
        """Search stable methodology notes, not live market evidence."""

        self._require(principal, SCOPE_KNOWLEDGE)
        requested_k = _bounded_top_k(top_k, maximum=5)
        text = _bounded_text(query, field="query")
        from backtest.strategy_knowledge import retrieve_backtest_knowledge

        return {
            "query": text,
            "top_k": requested_k,
            "content": retrieve_backtest_knowledge(text, k=requested_k),
            "evidence_class": "methodology_guidance",
            "notice": "This is general strategy methodology, not current market evidence.",
        }

    def search_session_document(
        self,
        principal: MCPPrincipal,
        *,
        session_id: str,
        query: str,
        top_k: int = 3,
    ) -> dict[str, Any]:
        """Retrieve only documents owned by the authenticated human user."""

        self._require(principal, SCOPE_DOCUMENT)
        if principal.is_service_account:
            raise MCPAuthorizationError("service accounts cannot access session documents")
        claimed_session = claim_session(session_id, principal.actor_id)
        self._authorize("document:read", "mcp_session_document", principal, claimed_session)
        requested_k = _bounded_top_k(top_k, maximum=_MAX_DOCUMENT_RESULTS)
        text = _bounded_text(query, field="query")
        context = retrieve_document_context(claimed_session, text, k=requested_k)
        return {
            "session_id": claimed_session,
            "query": text,
            "context": context,
            "citations": extract_document_citations(context) if context else [],
            "evidence_class": "session_document",
            "matched": bool(context),
        }
