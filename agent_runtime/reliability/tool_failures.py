"""Typed, bounded retries and circuit breaking for read-only tool calls.

The module is deliberately independent from a particular HTTP client.  Tool
implementations may raise exceptions or return ``{"ok": False, ...}``; both
paths are normalised into a small failure protocol that the Harness can trace
and use for a safe decision.
"""

from __future__ import annotations

import copy
import email.utils
import os
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - retained for the project's Python 3.10 compatibility path.
    class StrEnum(str, Enum):
        pass


class ErrorType(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    SCHEMA_VALIDATION = "SCHEMA_VALIDATION"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    NOT_FOUND = "NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    UPSTREAM_5XX = "UPSTREAM_5XX"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ToolFailure:
    error_type: ErrorType
    retryable: bool
    message: str
    retry_after_seconds: float | None = None
    status_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["error_type"] = self.error_type.value
        payload["next_action"] = self.next_action
        return payload

    @property
    def next_action(self) -> str:
        if self.retryable:
            return "retry_within_budget"
        if self.error_type in {ErrorType.INVALID_ARGUMENT, ErrorType.SCHEMA_VALIDATION}:
            return "repair_parameters_or_ask_user"
        if self.error_type in {ErrorType.PERMISSION_DENIED, ErrorType.AUTHENTICATION_FAILED}:
            return "reauthorize_or_reauthenticate"
        if self.error_type == ErrorType.NOT_FOUND:
            return "correct_target_or_ask_user"
        if self.error_type == ErrorType.CONTEXT_OVERFLOW:
            return "compact_context_once"
        if self.error_type == ErrorType.CIRCUIT_OPEN:
            return "use_marked_cache_or_stop"
        return "stop_and_report"


@dataclass
class RetryPolicy:
    """A bounded retry policy; defaults favour latency over repeated load."""

    max_attempts: int = field(default_factory=lambda: int(os.getenv("AGENT_TOOL_MAX_ATTEMPTS", "2")))
    initial_delay_seconds: float = field(
        default_factory=lambda: float(os.getenv("AGENT_TOOL_INITIAL_BACKOFF_SECONDS", "0.25"))
    )
    max_delay_seconds: float = field(
        default_factory=lambda: float(os.getenv("AGENT_TOOL_MAX_BACKOFF_SECONDS", "2.0"))
    )
    jitter_ratio: float = field(default_factory=lambda: float(os.getenv("AGENT_TOOL_JITTER_RATIO", "0.2")))

    def __post_init__(self) -> None:
        self.max_attempts = max(1, self.max_attempts)
        self.initial_delay_seconds = max(0.0, self.initial_delay_seconds)
        self.max_delay_seconds = max(self.initial_delay_seconds, self.max_delay_seconds)
        self.jitter_ratio = min(max(self.jitter_ratio, 0.0), 1.0)

    def delay_for(self, retry_number: int, retry_after_seconds: float | None, random_fn: Callable[[float, float], float]) -> float:
        if retry_after_seconds is not None:
            return min(max(0.0, retry_after_seconds), self.max_delay_seconds)
        base = min(self.max_delay_seconds, self.initial_delay_seconds * (2 ** max(0, retry_number - 1)))
        jitter = random_fn(0.0, base * self.jitter_ratio) if base and self.jitter_ratio else 0.0
        return min(self.max_delay_seconds, base + jitter)


@dataclass
class RetryBudget:
    """Per-run physical retry budget, shared by all logical tool calls."""

    max_retries: int = field(default_factory=lambda: int(os.getenv("AGENT_TOOL_MAX_RETRIES_PER_RUN", "3")))
    max_total_delay_seconds: float = field(
        default_factory=lambda: float(os.getenv("AGENT_TOOL_MAX_RETRY_DELAY_SECONDS", "4.0"))
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


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    open_until: float = 0.0


class CircuitBreakerRegistry:
    """Small process-local circuit breaker keyed by a stable tool name."""

    def __init__(self, *, failure_threshold: int | None = None, recovery_seconds: float | None = None):
        self.failure_threshold = failure_threshold or int(os.getenv("AGENT_TOOL_CIRCUIT_FAILURE_THRESHOLD", "3"))
        self.recovery_seconds = recovery_seconds or float(os.getenv("AGENT_TOOL_CIRCUIT_OPEN_SECONDS", "30"))
        self._states: dict[str, _CircuitState] = {}
        self._lock = threading.Lock()

    def remaining_open_seconds(self, tool_name: str, *, now: float | None = None) -> float | None:
        current = time.monotonic() if now is None else now
        with self._lock:
            state = self._states.get(tool_name)
            if not state or state.open_until <= current:
                return None
            return round(state.open_until - current, 3)

    def record_success(self, tool_name: str) -> None:
        with self._lock:
            self._states[tool_name] = _CircuitState()

    def record_failure(self, tool_name: str, *, transient: bool, now: float | None = None) -> bool:
        if not transient:
            return False
        current = time.monotonic() if now is None else now
        with self._lock:
            state = self._states.setdefault(tool_name, _CircuitState())
            state.consecutive_failures += 1
            if state.consecutive_failures >= max(1, self.failure_threshold):
                state.open_until = current + max(0.0, self.recovery_seconds)
                return True
            return False

    def reset(self) -> None:
        with self._lock:
            self._states.clear()


class ToolResultCache:
    """Per-run cache used only as an explicitly marked degraded read fallback."""

    def __init__(self, *, ttl_seconds: float | None = None, clock: Callable[[], float] = time.monotonic):
        self.ttl_seconds = ttl_seconds or float(os.getenv("AGENT_TOOL_CACHE_TTL_SECONDS", "60"))
        self._clock = clock
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def put(self, key: str, result: dict[str, Any]) -> None:
        if result.get("ok"):
            with self._lock:
                self._items[key] = (self._clock(), copy.deepcopy(result))

    def get_degraded(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            cached_at, value = item
            age = self._clock() - cached_at
            if age > self.ttl_seconds:
                self._items.pop(key, None)
                return None
            result = copy.deepcopy(value)
        result["degraded"] = True
        result["degradation_reason"] = "circuit_open_cached_result"
        result["source_kind"] = "degraded_cache"
        result["freshness"] = {**(result.get("freshness") or {}), "status": "cached", "cache_age_seconds": round(age, 3)}
        return result


DEFAULT_CIRCUITS = CircuitBreakerRegistry()
DEFAULT_TOOL_CACHE = ToolResultCache()


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


def _status_code(value: object) -> int | None:
    for candidate in (getattr(value, "status_code", None), getattr(getattr(value, "response", None), "status_code", None)):
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _retry_after(value: object) -> float | None:
    response = getattr(value, "response", None)
    headers = getattr(response, "headers", None) or getattr(value, "headers", None) or {}
    try:
        return _retry_after_seconds(headers.get("Retry-After"))
    except AttributeError:
        return None


def classify_tool_failure(value: object | None = None, *, result: dict[str, Any] | None = None) -> ToolFailure:
    """Classify exceptions and failed result contracts without client coupling."""
    if result and isinstance(result.get("tool_failure"), dict):
        existing = result["tool_failure"]
        try:
            error_type = ErrorType(str(existing.get("error_type", "UNKNOWN")))
        except ValueError:
            error_type = ErrorType.UNKNOWN
        return ToolFailure(
            error_type=error_type,
            retryable=bool(existing.get("retryable", False)),
            message=str(existing.get("message") or "tool reported a failure"),
            retry_after_seconds=_retry_after_seconds(existing.get("retry_after_seconds")),
            status_code=_status_code(value),
        )

    text = " ".join(
        str(item or "") for item in (
            value,
            result.get("error") if result else None,
            result.get("content") if result else None,
        )
    ).lower()
    status_code = _status_code(value)
    retry_after = _retry_after(value)
    if status_code == 429 or "rate limit" in text or "too many requests" in text:
        return ToolFailure(ErrorType.RATE_LIMITED, True, text[:300] or "rate limited", retry_after, status_code)
    if status_code and 500 <= status_code <= 599:
        return ToolFailure(ErrorType.UPSTREAM_5XX, True, text[:300] or "upstream server error", retry_after, status_code)
    if isinstance(value, TimeoutError) or any(token in text for token in ("timeout", "timed out", "read timeout", "connect timeout")):
        return ToolFailure(ErrorType.TIMEOUT, True, text[:300] or "tool timed out", retry_after, status_code)
    if isinstance(value, ConnectionError) or any(token in text for token in ("connection refused", "connection reset", "network is unreachable", "temporarily unavailable", "service unavailable")):
        return ToolFailure(ErrorType.UPSTREAM_UNAVAILABLE, True, text[:300] or "upstream unavailable", retry_after, status_code)
    if status_code in {401, 403} or any(token in text for token in ("permission denied", "forbidden", "unauthorized", "authentication")):
        error_type = ErrorType.PERMISSION_DENIED if status_code == 403 or "permission" in text or "forbidden" in text else ErrorType.AUTHENTICATION_FAILED
        return ToolFailure(error_type, False, text[:300] or "permission/authentication failed", None, status_code)
    if status_code == 404 or "not found" in text:
        return ToolFailure(ErrorType.NOT_FOUND, False, text[:300] or "resource not found", None, status_code)
    if any(token in text for token in ("invalid argument", "missing stock code", "missing parameter", "schema", "validation", "must be")):
        error_type = ErrorType.SCHEMA_VALIDATION if "schema" in text or "validation" in text else ErrorType.INVALID_ARGUMENT
        return ToolFailure(error_type, False, text[:300] or "invalid tool arguments", None, status_code)
    if any(token in text for token in ("context length", "context window", "too many tokens", "413")):
        return ToolFailure(ErrorType.CONTEXT_OVERFLOW, False, text[:300] or "context limit exceeded", None, status_code)
    return ToolFailure(ErrorType.UNKNOWN, False, text[:300] or "unclassified tool failure", retry_after, status_code)


def _failure_result(failure: ToolFailure, *, attempts: int, retry_trace: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": False,
        "content": f"[TOOL_ERROR] error_type={failure.error_type.value} retryable={str(failure.retryable).lower()} message={failure.message}",
        "tool_failure": failure.to_dict(),
        "attempts": attempts,
        "retry_trace": retry_trace,
    }


def invoke_with_failure_policy(
    tool_name: str,
    invoke: Callable[[], dict[str, Any]],
    *,
    cache_key: str,
    retry_budget: RetryBudget,
    retry_policy: RetryPolicy | None = None,
    circuits: CircuitBreakerRegistry | None = None,
    cache: ToolResultCache | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    random_fn: Callable[[float, float], float] = random.uniform,
) -> dict[str, Any]:
    """Call a read-only tool under error classification, retry and circuit rules.

    Returned fields are stable for trace persistence: ``attempts``,
    ``retry_trace``, optional ``tool_failure`` and optional ``degraded``.
    """
    policy = retry_policy or RetryPolicy()
    breaker = circuits or DEFAULT_CIRCUITS
    result_cache = cache or ToolResultCache()
    retry_trace: list[dict[str, Any]] = []
    open_for = breaker.remaining_open_seconds(tool_name)
    if open_for is not None:
        cached = result_cache.get_degraded(cache_key)
        if cached is not None:
            cached.update({"attempts": 0, "retry_trace": retry_trace, "circuit_state": "open_cached"})
            return cached
        failure = ToolFailure(
            ErrorType.CIRCUIT_OPEN,
            False,
            f"circuit is open for {open_for:.3f}s",
            retry_after_seconds=open_for,
        )
        result = _failure_result(failure, attempts=0, retry_trace=retry_trace)
        result["circuit_state"] = "open"
        return result

    attempts = 0
    last_failure: ToolFailure | None = None
    while attempts < policy.max_attempts:
        attempts += 1
        try:
            result = dict(invoke() or {})
            if result.get("ok"):
                result.update({"attempts": attempts, "retry_trace": retry_trace, "circuit_state": "closed"})
                breaker.record_success(tool_name)
                result_cache.put(cache_key, result)
                return result
            last_failure = classify_tool_failure(result=result)
        except Exception as exc:  # Boundary converts third-party errors into a contract.
            last_failure = classify_tool_failure(exc)

        assert last_failure is not None
        circuit_opened = breaker.record_failure(tool_name, transient=last_failure.retryable)
        if circuit_opened:
            retry_trace.append({"attempt": attempts, "event": "circuit_opened", "failure": last_failure.to_dict()})
            break
        if not last_failure.retryable or attempts >= policy.max_attempts:
            break
        retry_number = attempts
        delay = policy.delay_for(retry_number, last_failure.retry_after_seconds, random_fn)
        if not retry_budget.can_retry(delay):
            retry_trace.append({
                "attempt": attempts,
                "event": "retry_budget_exhausted",
                "failure": last_failure.to_dict(),
                "proposed_delay_seconds": round(delay, 4),
            })
            break
        retry_budget.consume(delay)
        retry_trace.append({
            "attempt": attempts,
            "event": "retry_scheduled",
            "failure": last_failure.to_dict(),
            "delay_seconds": round(delay, 4),
        })
        sleep_fn(delay)

    failure = last_failure or ToolFailure(ErrorType.UNKNOWN, False, "tool invocation did not return a result")
    result = _failure_result(failure, attempts=attempts, retry_trace=retry_trace)
    result["circuit_state"] = "open" if breaker.remaining_open_seconds(tool_name) is not None else "closed"
    return result
