from __future__ import annotations

import unittest

from agent_runtime.reliability.tool_failures import (
    CircuitBreakerRegistry,
    ErrorType,
    RetryBudget,
    RetryPolicy,
    ToolResultCache,
    classify_tool_failure,
    invoke_with_failure_policy,
)


class _RateLimitError(Exception):
    status_code = 429

    class response:
        status_code = 429
        headers = {"Retry-After": "1.5"}


class ToolFailureTests(unittest.TestCase):
    def setUp(self):
        self.policy = RetryPolicy(
            max_attempts=3,
            initial_delay_seconds=1.0,
            max_delay_seconds=5.0,
            jitter_ratio=0.0,
        )
        self.circuits = CircuitBreakerRegistry(failure_threshold=3, recovery_seconds=30)
        self.cache = ToolResultCache(ttl_seconds=60)
        self.sleeps = []

    def _call(self, tool_name, invoke, *, budget=None, cache_key="test"):
        return invoke_with_failure_policy(
            tool_name,
            invoke,
            cache_key=cache_key,
            retry_budget=budget or RetryBudget(max_retries=3, max_total_delay_seconds=10),
            retry_policy=self.policy,
            circuits=self.circuits,
            cache=self.cache,
            sleep_fn=self.sleeps.append,
            random_fn=lambda _low, _high: 0.0,
        )

    def test_deterministic_error_does_not_retry(self):
        calls = []

        result = self._call(
            "market-price",
            lambda: calls.append(1) or {"ok": False, "content": "[TOOL_ERROR] missing stock code"},
        )

        self.assertEqual(calls, [1])
        self.assertEqual(result["tool_failure"]["error_type"], ErrorType.INVALID_ARGUMENT.value)
        self.assertFalse(result["tool_failure"]["retryable"])
        self.assertEqual(result["retry_trace"], [])

    def test_timeout_retries_once_then_returns_success(self):
        attempts = []

        def invoke():
            attempts.append(1)
            if len(attempts) == 1:
                raise TimeoutError("read timed out")
            return {"ok": True, "content": "timestamped quote", "source_kind": "market_evidence"}

        result = self._call("market-price", invoke)

        self.assertEqual(len(attempts), 2)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(self.sleeps, [1.0])
        self.assertEqual(result["retry_trace"][0]["failure"]["error_type"], ErrorType.TIMEOUT.value)

    def test_rate_limit_honours_retry_after_before_retrying(self):
        attempts = []

        def invoke():
            attempts.append(1)
            if len(attempts) == 1:
                raise _RateLimitError("too many requests")
            return {"ok": True, "content": "ok"}

        result = self._call("stock-news", invoke)

        self.assertEqual(result["attempts"], 2)
        self.assertEqual(self.sleeps, [1.5])
        self.assertEqual(result["retry_trace"][0]["failure"]["error_type"], ErrorType.RATE_LIMITED.value)

    def test_total_retry_budget_stops_repeated_transient_failures(self):
        calls = []
        result = self._call(
            "financial-indicators",
            lambda: calls.append(1) or (_ for _ in ()).throw(TimeoutError("timeout")),
            budget=RetryBudget(max_retries=1, max_total_delay_seconds=0.5),
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["tool_failure"]["error_type"], ErrorType.TIMEOUT.value)
        self.assertEqual(result["retry_trace"][0]["event"], "retry_budget_exhausted")

    def test_open_circuit_returns_marked_cached_degradation_without_new_call(self):
        circuits = CircuitBreakerRegistry(failure_threshold=1, recovery_seconds=30)
        calls = []

        def success():
            calls.append("success")
            return {"ok": True, "content": "fresh quote", "source_kind": "market_evidence"}

        def failure():
            calls.append("failure")
            raise ConnectionError("service unavailable")

        self._call("market-price", success, cache_key="600519")
        invoke_with_failure_policy(
            "market-price",
            failure,
            cache_key="600519",
            retry_budget=RetryBudget(max_retries=0, max_total_delay_seconds=0),
            retry_policy=self.policy,
            circuits=circuits,
            cache=self.cache,
            sleep_fn=self.sleeps.append,
        )
        result = invoke_with_failure_policy(
            "market-price",
            lambda: calls.append("must-not-call") or {"ok": True},
            cache_key="600519",
            retry_budget=RetryBudget(max_retries=0, max_total_delay_seconds=0),
            retry_policy=self.policy,
            circuits=circuits,
            cache=self.cache,
            sleep_fn=self.sleeps.append,
        )

        self.assertEqual(calls, ["success", "failure"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["source_kind"], "degraded_cache")
        self.assertEqual(result["freshness"]["status"], "cached")
        self.assertEqual(result["circuit_state"], "open_cached")

    def test_classifier_keeps_unknown_failures_non_retryable(self):
        failure = classify_tool_failure(RuntimeError("unexpected parser state"))

        self.assertEqual(failure.error_type, ErrorType.UNKNOWN)
        self.assertFalse(failure.retryable)


if __name__ == "__main__":
    unittest.main()
