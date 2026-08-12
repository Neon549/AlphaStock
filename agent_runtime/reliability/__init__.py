"""Bounded failure handling for Agent tools and model-provider calls."""

from .tool_failures import (
    CircuitBreakerRegistry,
    DEFAULT_TOOL_CACHE,
    RetryBudget,
    RetryPolicy,
    ToolResultCache,
    classify_tool_failure,
    invoke_with_failure_policy,
)
from .model_failures import (
    DEFAULT_MODEL_CIRCUITS,
    ModelErrorType,
    ModelFailure,
    ModelInvocationError,
    ModelRetryBudget,
    ModelRetryPolicy,
    classify_model_failure,
    current_model_retry_budget,
    invoke_model_with_failure_policy,
    model_retry_budget_scope,
)

__all__ = [
    "CircuitBreakerRegistry",
    "DEFAULT_TOOL_CACHE",
    "RetryBudget",
    "RetryPolicy",
    "ToolResultCache",
    "classify_tool_failure",
    "invoke_with_failure_policy",
    "DEFAULT_MODEL_CIRCUITS",
    "ModelErrorType",
    "ModelFailure",
    "ModelInvocationError",
    "ModelRetryBudget",
    "ModelRetryPolicy",
    "classify_model_failure",
    "current_model_retry_budget",
    "invoke_model_with_failure_policy",
    "model_retry_budget_scope",
]
