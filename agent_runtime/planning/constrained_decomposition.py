"""Constrained LLM decomposition for complex, read-only research requests.

This is deliberately narrower than the primary intent parser.  It does not
classify permissions, cannot create a trade or publication task, and may only
reuse locally verified tickers already present in the original request.
"""

from __future__ import annotations

import json
import re
from typing import Any

from tools.stock_name_dict import get_stock_name


_CODE = re.compile(r"(?<!\d)([036]\d{5}|688\d{3})(?!\d)")
_FOCUSES = {"technical", "fundamental", "sentiment"}
_TASK_TYPES = {"investment_analysis", "comparison"}
_COMPLEX_MARKERS = ("比较", "对比", "现金流恶化", "为什么跌", "长期基本面", "如果", "条件")
_TRADE_MARKERS = ("下单", "买入", "卖出", "执行交易")
_MAX_TASKS = 3

DECOMPOSITION_PROMPT = """你是受约束的金融研究任务分解器。只返回 JSON，不能输出解释。
你只能使用用户原文中已经出现且由系统提供的股票代码；不能猜测代码、不能创建交易、发布、工具或权限任务。
可用 task_type 只有 investment_analysis 和 comparison。focus 只能从 technical、fundamental、sentiment 中选择。
depends_on 只能引用更早任务的 0 起始索引。最多 3 个任务。

JSON schema:
{{"tasks":[{{"task_type":"investment_analysis|comparison","stock_codes":["600519"],"focus":["fundamental"],"depends_on":[]}}]}}

用户原文：{query}
已验证且只能使用的股票代码：{stock_codes}
"""


def verified_codes_in_query(query: str) -> list[str]:
    """Return distinct static-dictionary codes explicitly present in the text."""
    return list(dict.fromkeys(
        code for code in _CODE.findall(query or "")
        if get_stock_name(code) != "名称未验证"
    ))


def needs_constrained_decomposition(query: str, *, known_stock_codes: list[str] | None = None) -> bool:
    """Only opt into an LLM for structures the deterministic grammar cannot express."""
    text = str(query or "")
    if not text or any(marker in text for marker in _TRADE_MARKERS):
        return False
    codes = list(dict.fromkeys([*(known_stock_codes or []), *verified_codes_in_query(text)]))
    return (len(codes) >= 2 and any(marker in text for marker in ("比较", "对比"))) or any(
        marker in text for marker in _COMPLEX_MARKERS
    )


def _parse_payload(raw: object) -> dict[str, Any]:
    value = re.sub(r"```(?:json)?|```", "", str(raw or "")).strip()
    payload = json.loads(value)
    if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
        raise ValueError("payload must contain a tasks list")
    return payload


def _normalise_tasks(payload: dict[str, Any], allowed_codes: list[str]) -> list[dict[str, Any]]:
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks or len(raw_tasks) > _MAX_TASKS:
        raise ValueError("task count is outside the allowed range")
    allowed = set(allowed_codes)
    tasks: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_tasks):
        if not isinstance(raw, dict):
            raise ValueError("task must be an object")
        task_type = str(raw.get("task_type") or "")
        if task_type not in _TASK_TYPES:
            raise ValueError("task type is not allowlisted")
        codes = raw.get("stock_codes")
        if not isinstance(codes, list) or not codes or any(not isinstance(code, str) or code not in allowed for code in codes):
            raise ValueError("task contains an unverified or invented stock code")
        codes = list(dict.fromkeys(codes))
        if task_type == "investment_analysis" and len(codes) != 1:
            raise ValueError("investment_analysis requires exactly one stock code")
        if task_type == "comparison" and len(codes) != 2:
            raise ValueError("comparison requires exactly two stock codes")
        focus = raw.get("focus") or []
        if not isinstance(focus, list) or any(item not in _FOCUSES for item in focus):
            raise ValueError("focus is invalid")
        focus = list(dict.fromkeys(focus)) or ["fundamental"]
        depends_on = raw.get("depends_on") or []
        if not isinstance(depends_on, list) or any(not isinstance(item, int) or item < 0 or item >= index for item in depends_on):
            raise ValueError("dependencies must point to earlier tasks")
        tasks.append({
            "task_type": task_type,
            "stock_codes": codes,
            "focus": focus,
            "depends_on": list(dict.fromkeys(depends_on)),
        })
    return tasks


def _to_sub_intents(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        intent = task["task_type"]
        task_id = f"{intent}-{index + 1}"
        codes = task["stock_codes"]
        focuses = task["focus"]
        slots: dict[str, Any] = {"stock_codes": codes, "analyst_focus": focuses[0] if len(focuses) == 1 else "all"}
        if intent == "investment_analysis":
            code = codes[0]
            slots.update({"stock_code": code, "stock_name": get_stock_name(code)})
        result.append({
            "task_id": task_id,
            "intent": intent,
            "depends_on": [f"{tasks[item]['task_type']}-{item + 1}" for item in task["depends_on"]],
            "slots": slots,
            "missing_slots": [],
            "requires_confirmation": False,
        })
    return result


def constrained_decompose(query: str, llm: Any, *, known_stock_codes: list[str] | None = None) -> dict[str, Any] | None:
    """Ask for a plan, validate every field, and return a task-graph payload.

    Invalid JSON, an unavailable model, an invented stock code, or an invalid
    dependency returns ``None``.  The caller must retain deterministic routing
    or clarification in those cases.
    """
    if not needs_constrained_decomposition(query, known_stock_codes=known_stock_codes):
        return None
    codes = list(dict.fromkeys([*(known_stock_codes or []), *verified_codes_in_query(query)]))
    codes = [code for code in codes if get_stock_name(code) != "名称未验证"]
    if not codes:
        return None
    try:
        raw = llm.invoke(DECOMPOSITION_PROMPT.format(query=query, stock_codes=", ".join(codes))).content
        tasks = _normalise_tasks(_parse_payload(raw), codes)
    except Exception:
        return None
    return {
        "sub_intents": _to_sub_intents(tasks),
        "original_query": query,
        "decomposition_source": "constrained_llm",
        "decomposition_reason": "complex_multi_entity_or_multihop_request",
    }
