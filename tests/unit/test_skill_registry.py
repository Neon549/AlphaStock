import unittest
from importlib import import_module

from agent_runtime.agents.skill_loader import get_skill_version, load_analyst_skill
from agent_runtime.skills.registry import skill_registry


class _FakeResponse:
    content = '{"skills":["document-rag"]}'


class _FakeLLM:
    def invoke(self, _prompt):
        return _FakeResponse()


class SkillRegistryTests(unittest.TestCase):
    def test_llm_can_select_only_registered_authorised_skill(self):
        selected = skill_registry.select(
            "根据已上传的年报分析宁德时代",
            context={"has_session_document": True},
            granted_permissions={"document:read"},
            llm=_FakeLLM(),
        )
        self.assertEqual([skill.name for skill in selected], ["document-rag"])

    def test_missing_permission_removes_skill_before_llm_selection(self):
        selected = skill_registry.select(
            "根据已上传的年报分析宁德时代",
            context={"has_session_document": True},
            granted_permissions=set(),
            llm=_FakeLLM(),
        )
        self.assertEqual(selected, [])

    def test_analysis_loader_is_registry_backed(self):
        self.assertIn("kdj", load_analyst_skill("technical").lower())
        self.assertTrue(get_skill_version("stock_analysis").startswith("stock-analysis@1.0.0+"))

    def test_document_rag_entrypoint_resolves_after_runtime_reorganisation(self):
        skill = skill_registry.get("document-rag")
        module_name, function_name = skill.entrypoint.split(":", 1)
        module = import_module(module_name)
        self.assertTrue(callable(getattr(module, function_name)))


if __name__ == "__main__":
    unittest.main()
