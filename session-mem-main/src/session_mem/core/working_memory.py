from __future__ import annotations

from dataclasses import dataclass, field

from session_mem.core.cell import MemoryCell


@dataclass
class WorkingMemory:
    """实际组装进 LLM Prompt 的最终上下文。"""

    hot_zone: list[str] = field(default_factory=list)
    activated_cells: list[MemoryCell] = field(default_factory=list)
    query: str = ""

    def to_prompt(self) -> list[dict[str, str]]:
        """按标准 OpenAI 消息格式返回上下文。"""
        parts: list[str] = []
        if self.hot_zone:
            parts.extend(self.hot_zone)
        for cell in self.activated_cells:
            if cell.raw_text:
                parts.append(cell.raw_text)
        if self.query:
            parts.append(self.query)

        content = "\n\n".join(parts)
        return [{"role": "user", "content": content}] if content else []
