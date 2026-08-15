"""Controlled taxonomy for reusable operating memory.

Memory is procedural/governance knowledge, never a store of live prices or
company facts. A controlled top-level taxonomy keeps retrieval, review and
evaluation slices meaningful once the corpus reaches thousands of files.
"""

from __future__ import annotations


MEMORY_SCOPES: dict[str, tuple[str, ...]] = {
    "governance": ("evidence_freshness", "publication_boundary", "risk_disclosure"),
    "research": ("source_conflict", "claim_validation", "document_reasoning"),
    "retrieval": ("query_routing", "citation_linking", "document_scope"),
    "workflow": ("routing", "fallback", "human_review"),
    "operations": ("tool_failure", "context_budget", "observability"),
    "backtest": ("data_contract", "strategy_validation", "optimizer_guard"),
    "evaluation": ("golden_set", "regression", "bad_case_triage"),
}

FORBIDDEN_MEMORY_CONTENT_HINTS = (
    "guaranteed return",
    "guaranteed profit",
    "real-time price",
    "current price",
    "latest price",
    "current financial",
    "api_key",
    "api key",
    "private key",
    "password",
    "access token",
    "secret",
    "social security",
    "phone number",
    "当前价格",
    "最新价格",
    "当前财务",
    "实时价格",
    "买入建议",
    "卖出建议",
    "买入该股",
    "卖出该股",
    "建议买入",
    "建议卖出",
    "buy this stock",
    "sell this stock",
    "investment recommendation",
    "目标价",
    "建议仓位",
    "身份证号",
    "手机号",
    "稳赚",
    "必涨",
    "当前股价",
    "实时价格",
)


def is_allowed_scope(scope: str) -> bool:
    return scope.strip().lower() in MEMORY_SCOPES


def allowed_scopes() -> tuple[str, ...]:
    return tuple(MEMORY_SCOPES)
