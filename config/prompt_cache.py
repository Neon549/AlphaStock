"""Prompt Cache 友好的消息组装。"""

from langchain_core.messages import HumanMessage, SystemMessage


def build_cacheable_messages(system_prompt: str, dynamic_content: str):
    """按“稳定前缀 → 动态后缀”返回 messages。

    固定规则必须在 ``system_prompt``；股票、RAG 证据和工具结果必须在
    ``dynamic_content``，从而不破坏 DeepSeek 可复用的共同前缀。
    """
    system_content = (system_prompt or "").strip()
    user_content = (dynamic_content or "").strip()
    if not system_content:
        raise ValueError("system_prompt 不能为空：固定规则不能静默丢失。")
    if not user_content:
        raise ValueError("dynamic_content 不能为空：请提供本轮任务或证据。")
    return [
        SystemMessage(content=system_content),
        HumanMessage(content=user_content),
    ]
