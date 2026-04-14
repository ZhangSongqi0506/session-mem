from __future__ import annotations

import pytest

from session_mem.core.buffer import SenMemBuffer, ShortMemBuffer, Turn
from session_mem.core.cell import MemoryCell
from session_mem.storage.base import CellStore


@pytest.fixture
def buffer() -> SenMemBuffer:
    b = SenMemBuffer(session_id="s1")
    b.set_token_estimator(lambda text: len(text) // 4)
    return b


class DummyCellStore(CellStore):
    def __init__(self):
        self._cells: dict[str, MemoryCell] = {}

    def save(self, cell: MemoryCell) -> None:
        self._cells[cell.id] = cell

    def get(self, cell_id: str) -> MemoryCell | None:
        return self._cells.get(cell_id)

    def list_by_session(self, session_id: str, limit: int | None = None):
        result = [c for c in self._cells.values() if c.session_id == session_id]
        if limit is not None:
            result = result[:limit]
        return result

    def find_by_entity(self, session_id: str, entity: str):
        return []

    def delete_session(self, session_id: str) -> None:
        to_remove = [cid for cid, c in self._cells.items() if c.session_id == session_id]
        for cid in to_remove:
            self._cells.pop(cid, None)


def test_estimated_tokens_with_estimator(buffer: SenMemBuffer) -> None:
    buffer.add_turn(Turn("user", "a" * 400, "2026-04-14T10:00:00Z"))
    assert buffer.estimated_tokens() == 100


def test_should_trigger_check_at_512(buffer: SenMemBuffer) -> None:
    for _ in range(5):
        buffer.add_turn(Turn("user", "x" * 400, "2026-04-14T10:00:00Z"))
    assert buffer.estimated_tokens() == 500
    assert not buffer.should_trigger_check()

    buffer.add_turn(Turn("user", "x" * 48, "2026-04-14T10:00:00Z"))
    assert buffer.estimated_tokens() == 512
    assert buffer.should_trigger_check()
    # 重复调用不应再次触发
    assert not buffer.should_trigger_check()


def test_should_trigger_check_at_1024(buffer: SenMemBuffer) -> None:
    for _ in range(10):
        buffer.add_turn(Turn("user", "x" * 400, "2026-04-14T10:00:00Z"))
    assert buffer.estimated_tokens() == 1000
    # 先触发 512 阈值
    assert buffer.should_trigger_check()
    assert not buffer.should_trigger_check()

    buffer.add_turn(Turn("user", "x" * 100, "2026-04-14T10:00:00Z"))
    assert buffer.should_trigger_check()  # 跨越 1024


def test_should_trigger_check_resets_after_extract(buffer: SenMemBuffer) -> None:
    for _ in range(6):
        buffer.add_turn(Turn("user", "x" * 400, "2026-04-14T10:00:00Z"))
    assert buffer.should_trigger_check()  # 600 >= 512

    buffer.extract_for_cell(3)
    assert buffer.estimated_tokens() == 300
    assert not buffer.should_trigger_check()

    for _ in range(3):
        buffer.add_turn(Turn("user", "x" * 400, "2026-04-14T10:00:01Z"))
    assert buffer.estimated_tokens() == 600
    assert buffer.should_trigger_check()


def test_is_hard_limit_reached(buffer: SenMemBuffer) -> None:
    for _ in range(20):
        buffer.add_turn(Turn("user", "x" * 400, "2026-04-14T10:00:00Z"))
    assert buffer.estimated_tokens() == 2000
    assert not buffer.is_hard_limit_reached()

    buffer.add_turn(Turn("user", "x" * 200, "2026-04-14T10:00:00Z"))
    assert buffer.estimated_tokens() == 2050
    assert buffer.is_hard_limit_reached()


def test_gap_detected_true() -> None:
    b = SenMemBuffer(session_id="s1")
    b.add_turn(Turn("user", "hi", "2026-04-14T10:00:00Z"))
    b.add_turn(Turn("assistant", "hello", "2026-04-14T10:35:00Z"))
    assert b.gap_detected()


def test_gap_detected_false() -> None:
    b = SenMemBuffer(session_id="s1")
    b.add_turn(Turn("user", "hi", "2026-04-14T10:00:00Z"))
    b.add_turn(Turn("assistant", "hello", "2026-04-14T10:05:00Z"))
    assert not b.gap_detected()


def test_gap_detected_iso_with_offset() -> None:
    b = SenMemBuffer(session_id="s1")
    b.add_turn(Turn("user", "hi", "2026-04-14T10:00:00+08:00"))
    b.add_turn(Turn("assistant", "hello", "2026-04-14T10:35:00+08:00"))
    assert b.gap_detected()


def test_extract_for_cell(buffer: SenMemBuffer) -> None:
    for i in range(5):
        role = "user" if i % 2 == 0 else "assistant"
        buffer.add_turn(Turn(role, str(i), "2026-04-14T10:00:00Z"))
    extracted = buffer.extract_for_cell(3)
    assert len(extracted) == 3
    assert len(buffer.turns) == 2
    assert extracted[0].content == "0"
    assert extracted[-1].content == "2"


def test_extract_for_cell_resets_check_count(buffer: SenMemBuffer) -> None:
    for _ in range(6):
        buffer.add_turn(Turn("user", "x" * 400, "2026-04-14T10:00:00Z"))
    assert buffer.should_trigger_check()
    buffer.extract_for_cell(3)
    # 重置后，添加一轮不应触发
    buffer.add_turn(Turn("user", "x" * 400, "2026-04-14T10:00:01Z"))
    assert not buffer.should_trigger_check()


def test_extract_segments_basic(buffer: SenMemBuffer) -> None:
    for i in range(8):
        role = "user" if i % 2 == 0 else "assistant"
        buffer.add_turn(Turn(role, str(i), "2026-04-14T10:00:00Z"))
    segments = buffer.extract_segments([3, 6])
    assert len(segments) == 2
    assert len(segments[0]) == 3
    assert len(segments[1]) == 3
    assert len(buffer.turns) == 2
    assert segments[0][0].content == "0"
    assert segments[0][-1].content == "2"
    assert segments[1][0].content == "3"
    assert segments[1][-1].content == "5"
    assert buffer.turns[0].content == "6"


def test_extract_segments_resets_check_count(buffer: SenMemBuffer) -> None:
    for _ in range(10):
        buffer.add_turn(Turn("user", "x" * 400, "2026-04-14T10:00:00Z"))
    assert buffer.should_trigger_check()  # 1000 >= 512
    buffer.extract_segments([3, 6])
    # 重置后，剩余 4 轮 = 400 tokens，再添加一轮不应触发
    buffer.add_turn(Turn("user", "x" * 400, "2026-04-14T10:00:01Z"))
    assert not buffer.should_trigger_check()


def test_extract_segments_empty_and_invalid() -> None:
    b = SenMemBuffer(session_id="s1")
    for i in range(3):
        b.add_turn(Turn("user", str(i), "2026-04-14T10:00:00Z"))
    assert b.extract_segments([]) == []
    assert b.extract_segments([0, -1, 100]) == []
    assert len(b.turns) == 3  # buffer 未被修改


# ============================================================
# ShortMemBuffer 测试
# ============================================================


def test_short_mem_buffer_all_cells_from_store() -> None:
    store = DummyCellStore()
    sb = ShortMemBuffer(session_id="s1", cell_store=store)

    cell = MemoryCell(
        id="C_001",
        session_id="s1",
        cell_type="fact",
        confidence=0.9,
        summary="test",
    )
    store.save(cell)

    cells = sb.all_cells()
    assert len(cells) == 1
    assert cells[0].id == "C_001"


def test_short_mem_buffer_add_and_get() -> None:
    store = DummyCellStore()
    sb = ShortMemBuffer(session_id="s1", cell_store=store)

    cell = MemoryCell(
        id="C_002",
        session_id="s1",
        cell_type="fact",
        confidence=0.8,
        summary="test2",
    )
    sb.add(cell)

    assert sb.get("C_002") is not None
    assert sb.get("C_002").id == "C_002"
    assert sb.get("C_999") is None
