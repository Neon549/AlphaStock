import json

from agent_runtime.planning.constrained_decomposition import constrained_decompose
from agent_runtime.planning.task_graph import build_task_dag


class _Response:
    def __init__(self, content: str):
        self.content = content


class _LLM:
    def __init__(self, payload: dict):
        self.payload = payload

    def invoke(self, _prompt: str):
        return _Response(json.dumps(self.payload, ensure_ascii=False))


def test_validated_comparison_can_only_reuse_original_codes() -> None:
    llm = _LLM({
        "tasks": [{
            "task_type": "comparison", "stock_codes": ["600519", "300750"],
            "focus": ["fundamental", "technical"], "depends_on": [],
        }]
    })

    result = constrained_decompose("比较 600519 和 300750 的基本面和走势", llm)

    assert result is not None
    task = result["sub_intents"][0]
    assert task["intent"] == "comparison"
    assert task["slots"]["stock_codes"] == ["600519", "300750"]
    assert build_task_dag(result["sub_intents"])["tasks"][0]["planned_status"] == "route_to_dedicated_endpoint"


def test_invented_code_or_invalid_dependency_fails_closed() -> None:
    invented = _LLM({
        "tasks": [{
            "task_type": "comparison", "stock_codes": ["600519", "000001"],
            "focus": ["fundamental"], "depends_on": [],
        }]
    })
    cyclic = _LLM({
        "tasks": [{
            "task_type": "investment_analysis", "stock_codes": ["600519"],
            "focus": ["fundamental"], "depends_on": [0],
        }]
    })

    assert constrained_decompose("比较 600519 和 300750", invented) is None
    assert constrained_decompose("如果 600519 现金流恶化，分析原因", cyclic) is None
