from __future__ import annotations

from typing import Callable

from session_mem.utils.tokenizer import TokenEstimator


class PromptAssembler:
    """组装全量历史基线 Prompt，用于 Token 节省率对比。"""

    def __init__(
        self,
        token_estimator: TokenEstimator | Callable[[str], int] | None = None,
    ):
        if token_estimator is None:
            self._estimator = TokenEstimator()
        else:
            self._estimator = token_estimator

    def _estimate(self, text: str) -> int:
        if callable(self._estimator) and not hasattr(self._estimator, "estimate"):
            return self._estimator(text)
        return self._estimator.estimate(text)

    def build_baseline(
        self,
        turns: list[dict[str, str]],
        query: str | None = None,
    ) -> tuple[list[dict[str, str]], int]:
        """
        将完整历史轮次直接拼接为 OpenAI 消息格式，并返回 token 数。

        Returns:
            messages: OpenAI 格式的消息列表
            tokens: 估算的 token 总数
        """
        return self._build(turns, query=query)

    def build_sliding_window(
        self,
        turns: list[dict[str, str]],
        query: str | None = None,
        window_size: int = 10,
    ) -> tuple[list[dict[str, str]], int]:
        """
        仅保留最近 window_size 轮对话作为滑窗基线，并返回 token 数。
        """
        window_turns = turns[-window_size:] if turns else []
        return self._build(window_turns, query=query)

    def _build(
        self,
        turns: list[dict[str, str]],
        query: str | None = None,
    ) -> tuple[list[dict[str, str]], int]:
        parts: list[str] = []
        for t in turns:
            role_label = t.get("role", "user").capitalize()
            parts.append(f"[{role_label}]: {t.get('content', '')}")

        if query:
            parts.append(f"[User]: {query}")

        content = "\n\n".join(parts)
        tokens = self._estimate(content)
        messages = [{"role": "user", "content": content}] if content else []
        return messages, tokens
