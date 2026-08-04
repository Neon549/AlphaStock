#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
@author: yulin
@created: 2026/4/18 22:44
@updated: 2026/4/18 22:44
@version: 1.0
@description: 
"""
from langchain_core.messages import BaseMessage


class ShortTermMemory:
    """
    管理单次分析会话的上下文
    核心问题：context window 有限，不能无限堆消息
    解决方案：只保留最近 N 条，超出则截断（对应知识库 8.5 上下文管理）
    """

    def __init__(self, max_messages: int = 20):
        self.max_messages = max_messages
        self.messages: list[BaseMessage] = []

    def add_message(self, message: BaseMessage):
        self.messages.append(message)
        # 超出上限时，保留第一条（系统消息）+ 最近 N-1 条
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[:1] + self.messages[-(self.max_messages - 1):]

    def get_messages(self) -> list[BaseMessage]:
        return self.messages

    def clear(self):
        self.messages = []

    def summary(self) -> str:
        return f"当前上下文：{len(self.messages)} 条消息"
