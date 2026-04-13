from __future__ import annotations

import tiktoken


class TokenEstimator:
    """基于 tiktoken 的 Token 估算器，支持多种编码 fallback。"""

    def __init__(self, model: str = "gpt-4o"):
        try:
            self._encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            self._encoding = tiktoken.get_encoding("cl100k_base")

    def estimate(self, text: str) -> int:
        return len(self._encoding.encode(text))
