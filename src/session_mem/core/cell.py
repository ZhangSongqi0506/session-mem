from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryCell:
    """记忆单元（Cell），承载从原始对话到结构化记忆的转化。"""

    id: str
    session_id: str
    cell_type: str  # fact | constraint | preference | task | fragmented
    confidence: float
    summary: str
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    linked_prev: str | None = None
    timestamp_start: str | None = None
    timestamp_end: str | None = None
    vector_id: str | None = None
    token_count: int = 0
    raw_text: str = ""
    # 关系层扩展
    causal_deps: list[str] = field(default_factory=list)  # 依赖的其他 Cell ID
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_retrieval_dict(self) -> dict[str, Any]:
        """用于检索层匹配的结构化表示。"""
        return {
            "id": self.id,
            "summary": self.summary,
            "keywords": self.keywords,
            "entities": self.entities,
            "cell_type": self.cell_type,
        }
