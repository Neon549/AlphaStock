import unittest

from agent_runtime.reliability.model_failures import (
    ModelErrorType,
    ModelInvocationError,
    ModelRetryBudget,
    ModelRetryPolicy,
    invoke_model_with_failure_policy,
)
from agent_runtime.reliability.tool_failures import CircuitBreakerRegistry


class _Response:
    def __init__(self, content: str):
        self.content = content


class _HttpError(RuntimeError):
    def __init__(self, status_code: int, message: str, retry_after: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.headers = {"Retry-After": retry_after} if retry_after else {}


class ModelFailurePolicyTests(unittest.TestCase):
    def test_transient_primary_failure_retries_before_returning_a_primary_response(self):
        calls = []
        delays = []
        events = []

        def primary():
            calls.append("primary")
            if len(calls) == 1:
                raise TimeoutError("provider timed out")
            return _Response("recovered")

        result = invoke_model_with_failure_policy(
            "planner",
            primary,
            primary_name="primary-model",
            retry_policy=ModelRetryPolicy(
                max_primary_attempts=2, max_backup_attempts=1,
                initial_delay_seconds=0.25, max_delay_seconds=1.0, jitter_ratio=0,
            ),
            retry_budget=ModelRetryBudget(max_retries=2, max_total_delay_seconds=1),
            circuits=CircuitBreakerRegistry(failure_threshold=5, recovery_seconds=10),
            sleep_fn=delays.append,
            on_attempt=events.append,
        )

        self.assertEqual(calls, ["primary", "primary"])
        self.assertEqual(delays, [0.25])
        self.assertFalse(result.used_backup)
        self.assertEqual(events[0]["failure"]["error_type"], "TIMEOUT")
        self.assertEqual(events[0]["recovery_action"], "retry_primary")

    def test_rate_limit_honours_retry_after_within_the_budget(self):
        calls = []
        delays = []

        def primary():
            calls.append("primary")
            if len(calls) == 1:
                raise _HttpError(429, "rate limit", retry_after="1.5")
            return _Response("recovered")

        invoke_model_with_failure_policy(
            "planner",
            primary,
            primary_name="primary-model",
            retry_policy=ModelRetryPolicy(
                max_primary_attempts=2, max_backup_attempts=1,
                initial_delay_seconds=0.1, max_delay_seconds=2, jitter_ratio=0,
            ),
            retry_budget=ModelRetryBudget(max_retries=1, max_total_delay_seconds=2),
            circuits=CircuitBreakerRegistry(failure_threshold=5, recovery_seconds=10),
            sleep_fn=delays.append,
        )

        self.assertEqual(delays, [1.5])

    def test_invalid_request_neither_retries_nor_switches_to_backup(self):
        backup_calls = []

        with self.assertRaises(ModelInvocationError) as raised:
            invoke_model_with_failure_policy(
                "planner",
                lambda: (_ for _ in ()).throw(ValueError("invalid request schema")),
                primary_name="primary-model",
                backup_invoke=lambda: backup_calls.append("backup") or _Response("should not run"),
                backup_name="backup-model",
                retry_policy=ModelRetryPolicy(max_primary_attempts=2, max_backup_attempts=1),
                retry_budget=ModelRetryBudget(max_retries=2, max_total_delay_seconds=2),
                circuits=CircuitBreakerRegistry(failure_threshold=5, recovery_seconds=10),
                sleep_fn=lambda _: None,
            )

        self.assertEqual(raised.exception.failure.error_type, ModelErrorType.INVALID_REQUEST)
        self.assertEqual(backup_calls, [])

    def test_open_primary_circuit_skips_provider_and_returns_draft_only_backup(self):
        circuits = CircuitBreakerRegistry(failure_threshold=1, recovery_seconds=60)
        primary_calls = []
        backup_calls = []
        policy = ModelRetryPolicy(max_primary_attempts=1, max_backup_attempts=1, initial_delay_seconds=0, max_delay_seconds=0)

        def unavailable_primary():
            primary_calls.append("primary")
            raise ConnectionError("service unavailable")

        # The first transient failure opens the circuit and uses a compatible backup.
        first = invoke_model_with_failure_policy(
            "deep",
            unavailable_primary,
            primary_name="primary-model",
            backup_invoke=lambda: backup_calls.append("backup") or _Response("draft"),
            backup_name="backup-model",
            fallback_mode="draft_only",
            retry_policy=policy,
            retry_budget=ModelRetryBudget(max_retries=1, max_total_delay_seconds=1),
            circuits=circuits,
            sleep_fn=lambda _: None,
        )
        second = invoke_model_with_failure_policy(
            "deep",
            lambda: primary_calls.append("unexpected") or _Response("should not run"),
            primary_name="primary-model",
            backup_invoke=lambda: backup_calls.append("backup") or _Response("draft"),
            backup_name="backup-model",
            fallback_mode="draft_only",
            retry_policy=policy,
            retry_budget=ModelRetryBudget(max_retries=1, max_total_delay_seconds=1),
            circuits=circuits,
            sleep_fn=lambda _: None,
        )

        self.assertTrue(first.used_backup)
        self.assertEqual(second.degradation_mode, "draft_only")
        self.assertEqual(primary_calls, ["primary"])
        self.assertEqual(backup_calls, ["backup", "backup"])
        self.assertEqual(second.attempt_trace[0]["failure"]["error_type"], "CIRCUIT_OPEN")

    def test_empty_primary_completion_can_recover_with_a_backup(self):
        result = invoke_model_with_failure_policy(
            "quick",
            lambda: _Response(""),
            primary_name="primary-model",
            backup_invoke=lambda: _Response("usable backup completion"),
            backup_name="backup-model",
            retry_policy=ModelRetryPolicy(max_primary_attempts=1, max_backup_attempts=1),
            retry_budget=ModelRetryBudget(max_retries=1, max_total_delay_seconds=1),
            circuits=CircuitBreakerRegistry(failure_threshold=5, recovery_seconds=10),
            sleep_fn=lambda _: None,
        )

        self.assertTrue(result.used_backup)
        self.assertEqual(result.result.content, "usable backup completion")
        self.assertEqual(result.attempt_trace[0]["failure"]["error_type"], "EMPTY_RESPONSE")


if __name__ == "__main__":
    unittest.main()
