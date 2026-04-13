from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from session_mem.core.cell import MemoryCell


class VectorIndex(ABC):
    """向量索引抽象：语义相似度检索。"""

    @abstractmethod
    def add(self, cell_id: str, embedding: list[float]) -> None:
        """添加向量。"""
        ...

    @abstractmethod
    def search(
        self, query_embedding: list[float], top_k: int = 5
    ) -> list[tuple[str, float]]:
        """返回 (cell_id, score) 列表。"""
        ...

    @abstractmethod
    def remove(self, cell_id: str) -> None:
        """删除向量。"""
        ...

    @abstractmethod
    def clear(self) -> None:
        """清空索引。"""
        ...


class CellStore(ABC):
    """Cell 元数据存储抽象。"""

    @abstractmethod
    def save(self, cell: MemoryCell) -> None:
        ...

    @abstractmethod
    def get(self, cell_id: str) -> MemoryCell | None:
        ...

    @abstractmethod
    def list_by_session(
        self, session_id: str, limit: int | None = None
    ) -> list[MemoryCell]:
        ...

    @abstractmethod
    def find_by_entity(self, session_id: str, entity: str) -> list[MemoryCell]:
        """实体共现检索。"""
        ...

    @abstractmethod
    def delete_session(self, session_id: str) -> None:
        ...


class TextStore(ABC):
    """原文存储抽象。"""

    @abstractmethod
    def save(self, cell_id: str, raw_text: str, token_count: int) -> None:
        ...

    @abstractmethod
    def load(self, cell_id: str) -> str:
        ...

    @abstractmethod
    def delete(self, cell_id: str) -> None:
        ...
