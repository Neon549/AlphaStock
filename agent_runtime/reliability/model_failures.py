"""Bounded, typed recovery for model-provider calls.

This module deliberately handles *provider availability*, not investment
correctness.  A non-empty provider response is the minimum transport contract;
node validators and the Output Gate remain responsible for evidence and
business-quality checks before any investment draft can progress.
"""

from __future__ import annotations

import email.utils
import os
import random
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterator

from .tool_failures import CircuitBreakerRegistry

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python 3.10 compatibility.
    class StrEnum(str, Enum):
        pass


class ModelErrorType(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_5XX = "UPSTREAM_5XX"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ModelFailure:
    error_type: ModelErrorType
    retryable: bool
    message: str
    retry_after_seconds: float | None = None
    status_code: int | None = None

    @property
    def next_action(self) -> str:
        if self.retryable:
            return "retry_within_model_budget_or_switch_compatible_backup"
        if self.error_type == ModelErrorType.CONTEXT_OVERFLOW:
            return "compact_context_once"
        if self.error_type in {ModelErrorType.AUTHENTICATION_FAILED, ModelErrorType.PERMISSION_DENIED}:
            return "repair_provider_configuration"
        if self.error_type == ModelErrorType.INVALID_REQUEST:
            return "correct_request_or_schema"
        if self.error_type == ModelErrorType.CIRCUIT_OPEN:
            return "switch_compatible_backup_or_stop"
        return "stop_and_report"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["error_type"] = self.error_type.value
        result["next_action"] = self.next_action
        return result


class EmptyModelResponseError(ValueError):
    """A provider accepted the request but returned no usable completion."""


class ModelInvocationError(RuntimeError):
    """Raised only after no safe provider recovery path remains."""

    def __init__(self, failure: ModelFailure, *, attempt_trace: list[dict[str, Any]]):
        self.failure = failure
        self.attempt_trace = attempt_trace
        super().__init__(
            f"model invocation failed: error_type={failure.error_type.value} "
            f"retryable={str(failure.retryable).lower()} message={failure.message}"
        )


@dataclass
class ModelRetryPolicy:
    """Per-provider attempt policy. Defaults prefer bounded latency and cost."""

    max_primary_attempts: int = field(
        default_factory=lambda: int(os.getenv("AGENT_MODEL_MAX_PRIMARY_ATTEMPTS", "2"))
    )
    max_backup_attempts: int = field(
        default_factory=lambda: int(os.getenv("AGENT_MODEL_MAX_BACKUP_ATTEMPTS", "1"))
    )
    initial_delay_seconds: float = field(
        default_factory=lambda: float(os.getenv("AGENT_MODEL_INITIAL_BACKOFF_SECONDS", "0.25"))
    )
    max_delay_seconds: float = field(
        default_factory=lambda: float(os.getenv("AGENT_MODEL_MAX_BACKOFF_SECONDS", "2.0"))
    )
    jitter_ratio: float = field(
        default_factory=lambda: float(os.getenv("AGENT_MODEL_JITTER_RATIO", "0.2"))
    )

    def __post_init__(self) -> None:
        self.max_primary_attempts = max(1, self.max_primary_attempts)
        self.max_backup_attempts = max(1, self.max_backup_attempts)
        self.initial_delay_seconds = max(0.0, self.initial_delay_seconds)
        self.max_delay_seconds = max(self.initial_delay_seconds, self.max_delay_seconds)
        self.jitter_ratio = min(max(self.jitter_ratio, 0.0), 1.0)

    def delay_for(
        self,
        retry_number: int,
        retry_after_seconds: float | None,
        random_fn: Callable[[float, float], float],
    ) -> float:
        if retry_after_seconds is not None:
            return min(max(0.0, retry_after_seconds), self.max_delay_seconds)
        base = min(self.max_delay_seconds, self.initial_delay_seconds * (2 ** max(0, retry_number - 1)))
        jitter = random_fn(0.0, base * self.jitter_ratio) if base and self.jitter_ratio else 0.0
        return min(self.max_delay_seconds, base + jitter)


@dataclass
class ModelRetryBudget:
    """Run-shared retry count and waiting-time budget for all model calls."""

    max_retries: int = field(
        default_factory=lambda: int(os.getenv("AGENT_MODEL_MAX_RETRIES_PER_RUN", "3"))
    )
    max_total_delay_seconds: float = field(
        default_factory=lambda: float(os.getenv("AGENT_MODEL_MAX_RETRY_DELAY_SECONDS", "4.0"))
    )
    retries_used: int = 0
    delay_used_seconds: float = 0.0

    def can_retry(self, delay_seconds: float) -> bool:
        return (
            self.retries_used < max(0, self.max_retries)
            and self.delay_used_seconds + delay_seconds <= max(0.0, self.max_total_delay_seconds)
        )

    def consume(self, delay_seconds: float) -> None:
        self.retries_used += 1
        self.delay_used_seconds += delay_seconds

    def summary(self) -> dict[str, Any]:
        return {
            "max_retries": self.max_retries,
            "retries_used": self.retries_used,
            "max_total_delay_seconds": self.max_total_delay_seconds,
            "delay_used_seconds": round(self.delay_used_seconds, 4),
        }


@dataclass(frozen=True)
class ModelInvocationResult:
    result: Any
    provider_role: str
    provider_name: str
    used_backup: bool
    degradation_mode: str
    attempt_trace: list[dict[str, Any]]


_ACTIVE_MODEL_RETRY_BUDGET: ContextVar[ModelRetryBudget | None] = ContextVar(
    "active_model_retry_budget", default=None
)


@contextmanager
def model_retry_budget_scope() -> Iterator[ModelRetryBudget]:
    """Share one bounded recovery budget across a Runtime model scope."""
    budget = ModelRetryBudget()
    token = _ACTIVE_MODEL_RETRY_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _ACTIVE_MODEL_RETRY_BUDGET.reset(token)


def current_model_retry_budget() -> ModelRetryBudget:
    """Return the run budget, or a bounded one-call budget for scripts/tests."""
    return _ACTIVE_MODEL_RETRY_BUDGET.get() or ModelRetryBudget()


DEFAULT_MODEL_CIRCUITS = CircuitBreakerRegistry(
    failure_threshold=int(os.getenv("AGENT_MODEL_CIRCUIT_FAILURE_THRESHOLD", "3")),
    recovery_seconds=float(os.getenv("AGENT_MODEL_CIRCUIT_OPEN_SECONDS", "30")),
)


def _status_code(value: object) -> int | None:
    for candidate in (
        getattr(value, "status_code", None),
        getattr(getattr(value, "response", None), "status_code", None),
    ):
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _retry_after_seconds(value: object) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(str(value)))
    except (TypeError, ValueError):
        pass
    try:
        retry_at = email.utils.parsedate_to_datetime(str(value))
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _retry_after(value: object) -> float | None:
    response = getattr(value, "response", None)
    headers = getattr(response, "headers", None) or getattr(value, "headers", None) or {}
    try:
        return _retry_after_seconds(headers.get("Retry-After"))
    except AttributeError:
        return None


def classify_model_failure(value: object) -> ModelFailure:
    """Classify provider failures without binding to one SDK or HTTP client."""
    if isinstance(value, ModelInvocationError):
        return value.failure
    if isinstance(value, EmptyModelResponseError):
        return ModelFailure(ModelErrorType.EMPTY_RESPONSE, True, str(value) or "empty model response")

    text = str(value or "").lower()
    status_code = _status_code(value)
    retry_after = _retry_after(value)
    if status_code == 429 or "rate limit" in text or "too many requests" in text:
        return ModelFailure(ModelErrorType.RATE_LIMITED, True, text[:300] or "model rate limited", retry_after, status_code)
    if status_code and 500 <= status_code <= 599:
        return ModelFailure(ModelErrorType.UPSTREAM_5XX, True, text[:300] or "model upstream 5xx", retry_after, status_code)
    if isinstance(value, TimeoutError) or any(token in text for token in ("timeout", "timed out", "read timeout", "connect timeout")):
        return ModelFailure(ModelErrorType.TIMEOUT, True, text[:300] or "model timed out", retry_after, status_code)
    if isinstance(value, ConnectionError) or any(token in text for token in (
        "connection refused", "connection reset", "network is unreachable", "temporarily unavailable", "service unavailable",
    )):
        return ModelFailure(ModelErrorType.UPSTREAM_UNAVAILABLE, True, text[:300] or "model provider unavailable", retry_after, status_code)
    if status_code in {401, 403} or any(token in text for token in ("permission denied", "forbidden", "unauthorized", "authentication")):
        error_type = ModelErrorType.PERMISSION_DENIED if status_code == 403 or "permission" in text or "forbidden" in text else ModelErrorType.AUTHENTICATION_FAILED
        return ModelFailure(error_type, False, text[:300] or "model provider authorization failed", None, status_code)
    if any(token in text for token in ("context length", "context window", "too many tokens", "maximum context", "413")):
        return ModelFailure(ModelErrorType.CONTEXT_OVERFLOW, False, text[:300] or "model context limit exceeded", None, status_code)
    if status_code == 400 or any(token in text for token in ("invalid request", "bad request", "invalid argument", "schema validation")):
        return ModelFailure(ModelErrorType.INVALID_REQUEST, False, text[:300] or "invalid model request", None, status_code)
    return ModelFailure(ModelErrorType.UNKNOWN, False, text[:300] or "unclassified model failure", retry_after, status_code)


def _assert_nonempty_result(result: Any) -> None:
    content = getattr(result, "content", result if isinstance(result, str) else None)
    if not isinstance(content, str) or not content.strip():
        raise EmptyModelResponseError("provider returned an empty completion")


def _emit(callback: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
    if callback is not None:
        callback(event)


def _invoke_provider(
    *,
    provider_role: str,
    provider_name: str,
    invoke: Callable[[], Any],
    max_attempts: int,
    policy: ModelRetryPolicy,
    retry_budget: ModelRetryBudget,
    circuits: CircuitBreakerRegistry,
    attempt_trace: list[dict[str, Any]],
    on_attempt: Callable[[dict[str, Any]], None] | None,
    sleep_fn: Callable[[float], None],
    random_fn: Callable[[float, float], float],
) -> tuple[Any | None, ModelFailure | None]:
    open_for = circuits.remaining_open_seconds(provider_name)
    if open_for is not None:
        failure = ModelFailure(
            ModelErrorType.CIRCUIT_OPEN,
            False,
            f"model circuit is open for {open_for:.3f}s",
            retry_after_seconds=open_for,
        )
        event = {
            "provider_role": provider_role,
            "provider_name": provider_name,
            "attempt": 0,
            "success": False,
            "failure": failure.to_dict(),
            "recovery_action": "switch_backup_or_stop",
            "circuit_state": "open",
            "latency_ms": 0.0,
        }
        attempt_trace.append(event)
        _emit(on_attempt, event)
        return None, failure

    last_failure: ModelFailure | None = None
    for provider_attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        try:
            result = invoke()
            _assert_nonempty_result(result)
        except Exception as exc:  # Third-party provider boundary.
            last_failure = classify_model_failure(exc)
            opened = circuits.record_failure(provider_name, transient=last_failure.retryable)
            event = {
                "provider_role": provider_role,
                "provider_name": provider_name,
                "attempt": provider_attempt,
                "success": False,
                "failure": last_failure.to_dict(),
                "recovery_action": "switch_backup_or_stop",
                "circuit_state": "open" if opened else "closed",
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
            }
            if last_failure.retryable and not opened and provider_attempt < max_attempts:
                delay = policy.delay_for(provider_attempt, last_failure.retry_after_seconds, random_fn)
                if retry_budget.can_retry(delay):
                    retry_budget.consume(delay)
                    event["recovery_action"] = "retry_primary" if provider_role == "primary" else "retry_backup"
                    event["retry_delay_seconds"] = round(delay, 4)
                    attempt_trace.append(event)
                    _emit(on_attempt, event)
                    sleep_fn(delay)
                    continue
                event["recovery_action"] = "retry_budget_exhausted"
            attempt_trace.append(event)
            _emit(on_attempt, event)
            return None, last_failure

        circuits.record_success(provider_name)
        event = {
            "provider_role": provider_role,
            "provider_name": provider_name,
            "attempt": provider_attempt,
            "success": True,
            "result": result,
            "recovery_action": "primary_success" if provider_role == "primary" else "backup_success",
            "circuit_state": "closed",
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
        }
        attempt_trace.append(event)
        _emit(on_attempt, event)
        return result, None

    return None, last_failure


def invoke_model_with_failure_policy(
    logical_name: str,
    primary_invoke: Callable[[], Any],
    *,
    primary_name: str,
    backup_invoke: Callable[[], Any] | None = None,
    backup_name: str | None = None,
    fallback_mode: str = "full",
    retry_policy: ModelRetryPolicy | None = None,
    retry_budget: ModelRetryBudget | None = None,
    circuits: CircuitBreakerRegistry | None = None,
    on_attempt: Callable[[dict[str, Any]], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    random_fn: Callable[[float, float], float] = random.uniform,
) -> ModelInvocationResult:
    """Invoke a primary model with typed retry, circuit and compatible failover.

    Only transient provider failures may retry or switch model.  Invalid input,
    context overflow and authorization errors are surfaced to the caller so it
    can compact, fix configuration or block safely instead of hiding the bug
    behind a different model.
    """
    if fallback_mode not in {"full", "draft_only"}:
        raise ValueError("fallback_mode must be 'full' or 'draft_only'")
    del logical_name  # Retained in the API for readable call sites and future policy routing.
    policy = retry_policy or ModelRetryPolicy()
    budget = retry_budget or current_model_retry_budget()
    breaker = circuits or DEFAULT_MODEL_CIRCUITS
    attempt_trace: list[dict[str, Any]] = []

    result, primary_failure = _invoke_provider(
        provider_role="primary",
        provider_name=primary_name,
        invoke=primary_invoke,
        max_attempts=policy.max_primary_attempts,
        policy=policy,
        retry_budget=budget,
        circuits=breaker,
        attempt_trace=attempt_trace,
        on_attempt=on_attempt,
        sleep_fn=sleep_fn,
        random_fn=random_fn,
    )
    if result is not None:
        return ModelInvocationResult(result, "primary", primary_name, False, "none", attempt_trace)

    assert primary_failure is not None
    may_fail_over = primary_failure.retryable or primary_failure.error_type == ModelErrorType.CIRCUIT_OPEN
    if not (may_fail_over and backup_invoke is not None and backup_name):
        raise ModelInvocationError(primary_failure, attempt_trace=attempt_trace)

    result, backup_failure = _invoke_provider(
        provider_role="backup",
        provider_name=backup_name,
        invoke=backup_invoke,
        max_attempts=policy.max_backup_attempts,
        policy=policy,
        retry_budget=budget,
        circuits=breaker,
        attempt_trace=attempt_trace,
        on_attempt=on_attempt,
        sleep_fn=sleep_fn,
        random_fn=random_fn,
    )
    if result is not None:
        return ModelInvocationResult(result, "backup", backup_name, True, fallback_mode, attempt_trace)

    raise ModelInvocationError(backup_failure or primary_failure, attempt_trace=attempt_trace)
