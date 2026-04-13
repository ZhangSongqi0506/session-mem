from __future__ import annotations

import os
from typing import Any

from openai import OpenAI

from .base import LLMClient


class QwenClient(LLMClient):
    """内网 qwen2.5:72b 客户端（OpenAI 兼容接口）。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "qwen2.5:72b-instruct-nq",
    ):
        self.api_key = api_key or os.getenv(
            "SESSION_MEM_API_LLM_API_KEY", "not-needed"
        )
        self.base_url = base_url or os.getenv(
            "SESSION_MEM_API_LLM_BASE_URL", "http://172.10.10.200/v1"
        )
        self.model = model
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        response_format: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> str:
        extra = {}
        if response_format:
            extra["response_format"] = response_format
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            **extra,
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    def isolated_chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        response_format: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> str:
        """
        qwen2.5:72b 本身是无状态的，独立新会话只需确保 messages 中
        仅包含本次请求所需的 system + user 内容即可。
        调用方（如语义边界检测器）应自行保证 messages 的隔离性。
        """
        return self.chat_completion(
            messages=messages,
            temperature=temperature,
            response_format=response_format,
            **kwargs,
        )
