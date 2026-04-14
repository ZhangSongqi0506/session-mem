from __future__ import annotations


from session_mem.core.buffer import Turn
from session_mem.core.cell_generator import CellGenerator
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


def test_generate_success() -> None:
    llm = MockLLM(
        '{"summary": "用户询问天气", "keywords": ["天气", "北京"], '
        '"entities": ["北京"], "cell_type": "fact", "confidence": 0.9, "causal_deps": []}'
    )
    gen = CellGenerator(llm)
    turns = [Turn("user", "今天北京天气怎么样？", "2026-04-14T10:00:00Z")]
    cell = gen.generate(turns, "s1", "C_001")

    assert cell.summary == "用户询问天气"
    assert cell.keywords == ["天气", "北京"]
    assert cell.entities == ["北京"]
    assert cell.cell_type == "fact"
    assert cell.confidence == 0.9
    assert cell.token_count > 0
    assert "今天北京天气怎么样？" in cell.raw_text


def test_generate_llm_empty_fallback() -> None:
    llm = MockLLM("")
    gen = CellGenerator(llm)
    turns = [
        Turn("user", "帮我订一张去上海的机票", "2026-04-14T10:00:00Z"),
        Turn("assistant", "好的，请问您需要什么时间？", "2026-04-14T10:01:00Z"),
    ]
    cell = gen.generate(turns, "s1", "C_002")

    assert cell.summary != ""
    assert len(cell.keywords) > 0
    assert len(cell.entities) > 0
    assert cell.confidence <= 0.3  # fallback 置信度较低
    assert cell.cell_type == "fact"


def test_generate_json_with_code_block() -> None:
    llm = MockLLM(
        "```json\n"
        '{"summary": "预订机票", "keywords": ["机票", "上海"], '
        '"entities": ["上海"], "cell_type": "task", "confidence": 0.85, "causal_deps": []}\n'
        "```"
    )
    gen = CellGenerator(llm)
    turns = [Turn("user", "订机票", "2026-04-14T10:00:00Z")]
    cell = gen.generate(turns, "s1", "C_003")

    assert cell.summary == "预订机票"
    assert cell.cell_type == "task"


def test_generate_linked_prev() -> None:
    llm = MockLLM(
        '{"summary": "继续", "keywords": ["继续"], '
        '"entities": [], "cell_type": "fact", "confidence": 0.5, "causal_deps": []}'
    )
    gen = CellGenerator(llm)
    turns = [Turn("user", "继续", "2026-04-14T10:00:00Z")]
    cell = gen.generate(turns, "s1", "C_004", linked_prev="C_003")

    assert cell.linked_prev == "C_003"
