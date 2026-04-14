from __future__ import annotations

import logging

from session_mem.core.buffer import Turn
from session_mem.llm.base import LLMClient
from session_mem.llm.parser import safe_json_loads
from session_mem.llm.prompts import build_boundary_prompt

logger = logging.getLogger(__name__)


class SemanticBoundaryDetector:
    """
    语义边界检测器：调用 qwen2.5:72b 以独立新会话判断对话中的语义切分点。
    """

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def should_split(self, turns: list[Turn]) -> list[int]:
        """
        返回语义边界切分点索引列表。

        切分点索引表示在该轮次**之后**进行切分。例如 [3, 6] 表示：
        - 第 1-3 轮生成第一个 Cell
        - 第 4-6 轮生成第二个 Cell
        - 第 7 轮及之后保留在 Buffer 中继续累积

        Fallback 规则：
        - 内容过长（字符数超过约 2048 tokens 对应量）时直接切分全部；
        - LLM 调用异常时返回空列表，避免流程中断。
        """
        if not turns:
            return []

        total_chars = sum(len(t.content) for t in turns)
        if total_chars > 8000:
            # Fallback：内容过长时强制切分，保留最后一轮作为新种子（与旧行为一致）
            return [max(1, len(turns) - 1)]

        prompt_turns = [{"role": t.role, "content": t.content} for t in turns]
        messages = build_boundary_prompt(prompt_turns)
        try:
            response = self.llm.isolated_chat(messages, temperature=0.1)
            return _parse_split_indices(response, len(turns))
        except Exception as exc:
            logger.warning("Boundary detection failed: %s", exc)
            return []


def _parse_split_indices(response: str, max_index: int) -> list[int]:
    """解析 LLM 返回的切分点索引列表。"""
    data = safe_json_loads(response)
    indices: list[int] = []

    if isinstance(data, dict):
        raw = data.get("split_indices") or data.get("split_points") or data.get("boundaries") or []
        if isinstance(raw, list):
            indices = [int(x) for x in raw if isinstance(x, (int, float)) and x > 0]
    elif isinstance(data, list):
        indices = [int(x) for x in data if isinstance(x, (int, float)) and x > 0]

    # 去重、排序、过滤越界
    indices = sorted({i for i in indices if 0 < i <= max_index})
    return indices
