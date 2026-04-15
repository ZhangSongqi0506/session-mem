from __future__ import annotations

from session_mem.utils.tokenizer import TokenEstimator


class PromptAssembler:
    """组装全量历史基线 Prompt，用于 Token 节省率对比。"""

    def __init__(self, token_estimator: TokenEstimator | None = None):
        self._estimator = token_estimator or TokenEstimator()

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
        parts: list[str] = []
        for t in turns:
            role_label = t.get("role", "user").capitalize()
            parts.append(f"[{role_label}]: {t.get('content', '')}")

        if query:
            parts.append(f"[User]: {query}")

        content = "\n\n".join(parts)
        tokens = self._estimator.estimate(content)
        messages = [{"role": "user", "content": content}] if content else []
        return messages, tokens
