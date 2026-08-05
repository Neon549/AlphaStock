import unittest
from pathlib import Path

from control_plane.security import (
    PermissionDecision,
    PermissionMode,
    PermissionRule,
    SecurityOperation,
    evaluate_permission,
)


class PermissionPipelineTests(unittest.TestCase):
    def test_deny_rule_is_not_overridden_by_bypass(self):
        result = evaluate_permission(
            SecurityOperation("shell", "rm -rf build"),
            mode=PermissionMode.BYPASS,
            rules=[PermissionRule(PermissionDecision.DENY, "shell(*)")],
        )
        self.assertEqual(result.decision, PermissionDecision.DENY)
        self.assertEqual(result.stage, "rules")

    def test_path_escape_is_bypass_immune(self):
        result = evaluate_permission(
            SecurityOperation("write", "../../outside.txt"),
            mode=PermissionMode.BYPASS,
            rules=[],
        )
        self.assertEqual(result.decision, PermissionDecision.DENY)
        self.assertTrue(result.bypass_immune)

    def test_sensitive_path_is_blocked_even_with_allow_rule(self):
        result = evaluate_permission(
            SecurityOperation("write", ".env.production"),
            mode=PermissionMode.BYPASS,
            rules=[PermissionRule(PermissionDecision.ALLOW, "write(*)")],
        )
        self.assertEqual(result.decision, PermissionDecision.DENY)

    def test_auto_mode_has_fast_path_and_fail_closed_fallback(self):
        safe = evaluate_permission(SecurityOperation("read", "README.md"), mode=PermissionMode.AUTO, rules=[])
        unknown = evaluate_permission(SecurityOperation("unknown", "external-resource"), mode=PermissionMode.AUTO, rules=[])
        self.assertEqual(safe.decision, PermissionDecision.ALLOW)
        self.assertEqual(unknown.decision, PermissionDecision.DENY)
        self.assertEqual(unknown.stage, "dynamic_stage_2")

    def test_project_edit_is_allowed_in_accept_edits(self):
        result = evaluate_permission(
            SecurityOperation("write", str(Path("reports") / "draft.md")),
            mode=PermissionMode.ACCEPT_EDITS,
            rules=[],
        )
        self.assertEqual(result.decision, PermissionDecision.ALLOW)


if __name__ == "__main__":
    unittest.main()
