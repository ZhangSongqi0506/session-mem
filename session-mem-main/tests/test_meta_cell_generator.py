from __future__ import annotations

import pytest

from session_mem.core.cell import MemoryCell
from session_mem.core.meta_cell_generator import MetaCellGenerator
from session_mem.llm.base import LLMClient


class MockLLM(LLMClient):
    def __init__(self, response: str = ""):
        self.response = response

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        response_format: dict[str, str] | None = None,
        **kwargs,
    ) -> str:
        return self.response


def test_initial_meta_cell() -> None:
    llm = MockLLM(
        '{"summary": "用户在询问天气和交通", "keywords": ["天气", "交通"], '
        '"entities": ["北京"], "confidence": 0.8, "causal_deps": []}'
    )
    gen = MetaCellGenerator(llm)
    cells = [
        MemoryCell(
            id="C_001",
            session_id="s1",
            cell_type="fact",
            confidence=0.9,
            summary="用户询问北京天气",
            keywords=["天气", "北京"],
            entities=["北京"],
            raw_text="今天北京天气怎么样？",
        )
    ]
    meta = gen.generate("s1", cells)

    assert meta.cell_type == "meta"
    assert meta.id == "M_001"
    assert meta.version == 1
    assert meta.status == "active"
    assert meta.linked_cells == ["C_001"]
    assert meta.summary == "用户在询问天气和交通"


def test_update_meta_cell() -> None:
    llm = MockLLM(
        '{"summary": "用户计划北京行程，关注天气和交通", "keywords": ["天气", "交通", "北京"], '
        '"entities": ["北京"], "confidence": 0.85, "causal_deps": []}'
    )
    gen = MetaCellGenerator(llm)
    cells = [
        MemoryCell(
            id="C_001",
            session_id="s1",
            cell_type="fact",
            confidence=0.9,
            summary="用户询问北京天气",
            keywords=["天气", "北京"],
            entities=["北京"],
            raw_text="今天北京天气怎么样？",
        ),
        MemoryCell(
            id="C_002",
            session_id="s1",
            cell_type="task",
            confidence=0.9,
            summary="用户查询北京交通",
            keywords=["交通", "北京"],
            entities=["北京"],
            raw_text="北京地铁怎么坐？",
        ),
    ]
    previous_meta = MemoryCell(
        id="M_001",
        session_id="s1",
        cell_type="meta",
        confidence=0.8,
        summary="用户询问北京天气",
        keywords=["天气", "北京"],
        entities=["北京"],
        version=1,
        status="active",
        linked_cells=["C_001"],
        raw_text="用户询问北京天气",
    )
    meta = gen.generate("s1", cells, previous_meta=previous_meta)

    assert meta.id == "M_002"
    assert meta.version == 2
    assert meta.status == "active"
    assert meta.linked_cells == ["C_001", "C_002"]
    assert "行程" in meta.summary


def test_meta_cell_fallback_on_llm_failure() -> None:
    llm = MockLLM("")
    gen = MetaCellGenerator(llm)
    cells = [
        MemoryCell(
            id="C_001",
            session_id="s1",
            cell_type="fact",
            confidence=0.9,
            summary="摘要 A",
            keywords=["kw1", "kw2"],
            entities=["ent1"],
            raw_text="...",
        ),
        MemoryCell(
            id="C_002",
            session_id="s1",
            cell_type="fact",
            confidence=0.8,
            summary="摘要 B",
            keywords=["kw2", "kw3"],
            entities=["ent2"],
            raw_text="...",
        ),
    ]
    meta = gen.generate("s1", cells)

    assert meta.cell_type == "meta"
    assert meta.summary != ""
    assert len(meta.keywords) > 0
    assert len(meta.entities) > 0
    assert meta.confidence <= 0.3


def test_meta_cell_empty_cells_raises() -> None:
    llm = MockLLM("{}")
    gen = MetaCellGenerator(llm)
    with pytest.raises(ValueError):
        gen.generate("s1", [])
