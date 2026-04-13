from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    """LLM 调用抽象，支持主会话调用和独立新会话调用。"""

    @abstractmethod
    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        response_format: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> str:
        """标准聊天补全，走主会话或默认连接。"""
        ...

    def isolated_chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        response_format: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> str:
        """
        独立新会话调用，不带任何历史上下文。
        默认实现可复用 chat_completion（若模型本身无状态）；
        有状态模型（如某些 Agent API）可在此覆写以强制开启新 session。
        """
        return self.chat_completion(
            messages=messages,
            temperature=temperature,
            response_format=response_format,
            **kwargs,
        )
