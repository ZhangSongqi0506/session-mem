from __future__ import annotations

import pytest

from session_mem.core.memory_system import MemorySystem
from session_mem.llm.base import LLMClient
from session_mem.storage.base import CellStore, TextStore, VectorIndex


class DummyVectorIndex(VectorIndex):
    def __init__(self):
        self._data: dict[str, list[float]] = {}

    def add(self, cell_id: str, embedding: list[float]) -> None:
        self._data[cell_id] = embedding

    def search(self, query_embedding: list[float], top_k: int = 5) -> list[tuple[str, float]]:
        return []

    def remove(self, cell_id: str) -> None:
        self._data.pop(cell_id, None)

    def clear(self) -> None:
        self._data.clear()


class DummyCellStore(CellStore):
    def __init__(self):
        self._cells: dict[str, object] = {}

    def save(self, cell) -> None:
        self._cells[cell.id] = cell

    def get(self, cell_id: str):
        return self._cells.get(cell_id)

    def list_by_session(self, session_id: str, limit: int | None = None):
        return [c for c in self._cells.values() if c.session_id == session_id][:limit]

    def find_by_entity(self, session_id: str, entity: str):
        return []

    def delete_session(self, session_id: str) -> None:
        to_remove = [cid for cid, c in self._cells.items() if c.session_id == session_id]
        for cid in to_remove:
            self._cells.pop(cid, None)


class DummyTextStore(TextStore):
    def __init__(self):
        self._texts: dict[str, tuple[str, int]] = {}

    def save(self, cell_id: str, raw_text: str, token_count: int) -> None:
        self._texts[cell_id] = (raw_text, token_count)

    def load(self, cell_id: str) -> str:
        return self._texts.get(cell_id, ("", 0))[0]

    def delete(self, cell_id: str) -> None:
        self._texts.pop(cell_id, None)


class MockLLM(LLMClient):
    def __init__(self, response: str = "CONTINUE"):
        self.response = response

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        response_format: dict[str, str] | None = None,
        **kwargs,
    ) -> str:
        return self.response


@pytest.fixture
def ms():
    return MemorySystem(
        session_id="s1",
        llm_client=MockLLM("CONTINUE"),
        vector_index=DummyVectorIndex(),
        cell_store=DummyCellStore(),
        text_store=DummyTextStore(),
    )


def test_add_turn_basic(ms: MemorySystem) -> None:
    ms.add_turn("user", "hello", "2026-04-14T10:00:00Z")
    assert len(ms.sen_buffer.turns) == 1


def test_gap_detected_forces_split(ms: MemorySystem) -> None:
    ms.add_turn("user", "hello", "2026-04-14T10:00:00Z")
    ms.add_turn("assistant", "hi there", "2026-04-14T10:35:00Z")
    # gap_detected 触发强制切分
    assert len(ms.sen_buffer.turns) == 0
    assert len(ms.short_buffer.all_cells()) == 1
    cell = ms.short_buffer.all_cells()[0]
    assert cell.cell_type != "fragmented"


def test_hard_limit_forces_fragmented(ms: MemorySystem) -> None:
    # 使用可控的 token estimator，使每轮约 205 tokens，10 轮 = 2050 > 2048
    ms.sen_buffer.set_token_estimator(lambda text: len(text) // 4)
    for i in range(10):
        ms.add_turn("user" if i % 2 == 0 else "assistant", "x" * 820, "2026-04-14T10:00:00Z")
    assert len(ms.sen_buffer.turns) == 0
    assert len(ms.short_buffer.all_cells()) == 1
    cell = ms.short_buffer.all_cells()[0]
    assert cell.cell_type == "fragmented"


def test_soft_limit_boundary_split(ms: MemorySystem) -> None:
    # 使用可控的 token estimator，使每轮约 100 tokens
    ms.sen_buffer.set_token_estimator(lambda text: len(text) // 4)
    # 将边界检测器替换为总是 SPLIT
    ms.boundary_detector = object.__new__(type(ms.boundary_detector))
    ms.boundary_detector.llm = MockLLM("SPLIT")
    ms.boundary_detector.should_split = lambda turns: True

    # 6 轮 = 600 >= 512
    for i in range(6):
        ms.add_turn("user" if i % 2 == 0 else "assistant", "x" * 400, "2026-04-14T10:00:00Z")

    # 触发切分，保留最后一轮
    assert len(ms.short_buffer.all_cells()) == 1
    assert len(ms.sen_buffer.turns) == 1
    cell = ms.short_buffer.all_cells()[0]
    assert cell.cell_type != "fragmented"


def test_linked_prev_chain(ms: MemorySystem) -> None:
    # 时间间隔强制切分两次
    ms.add_turn("user", "hello", "2026-04-14T10:00:00Z")
    ms.add_turn("assistant", "hi", "2026-04-14T10:35:00Z")
    first_id = ms.short_buffer.all_cells()[0].id

    ms.add_turn("user", "world", "2026-04-14T11:00:00Z")
    ms.add_turn("assistant", "hey", "2026-04-14T11:35:00Z")
    cells = ms.short_buffer.all_cells()
    assert cells[1].linked_prev == first_id
