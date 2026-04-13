from __future__ import annotations

from session_mem.core.buffer import Turn
from session_mem.llm.base import LLMClient
from session_mem.llm.prompts import build_boundary_prompt


class SemanticBoundaryDetector:
    """
    语义边界检测器：调用 qwen2.5:72b 以独立新会话判断对话是否应切分。
    """

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def should_split(self, turns: list[Turn]) -> bool:
        """
        返回 True 表示检测到语义边界，应切分 Cell。
        """
        if not turns:
            return False

        prompt_turns = [
            {"role": t.role, "content": t.content} for t in turns
        ]
        messages = build_boundary_prompt(prompt_turns)
        response = self.llm.isolated_chat(messages, temperature=0.1)
        return "SPLIT" in response.upper()
