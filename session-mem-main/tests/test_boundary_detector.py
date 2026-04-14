from __future__ import annotations


from session_mem.core.buffer import Turn
from session_mem.core.boundary_detector import SemanticBoundaryDetector, _parse_split_indices
from session_mem.llm.base import LLMClient


class MockLLM(LLMClient):
    def __init__(self, response: str):
        self.response = response

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        response_format: dict[str, str] | None = None,
        **kwargs,
    ) -> str:
        return self.response


class FailingLLM(LLMClient):
    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        response_format: dict[str, str] | None = None,
        **kwargs,
    ) -> str:
        raise RuntimeError("LLM service unavailable")


def test_should_split_returns_indices_on_json() -> None:
    detector = SemanticBoundaryDetector(MockLLM('{"split_indices": [2, 4]}'))
    turns = [Turn("user", f"turn {i}", "2026-04-14T10:00:00Z") for i in range(6)]
    assert detector.should_split(turns) == [2, 4]


def test_should_split_returns_empty_on_continue() -> None:
    detector = SemanticBoundaryDetector(MockLLM('{"split_indices": []}'))
    turns = [Turn("user", "hello", "2026-04-14T10:00:00Z")]
    assert detector.should_split(turns) == []


def test_should_split_empty_turns() -> None:
    detector = SemanticBoundaryDetector(MockLLM('{"split_indices": [1]}'))
    assert detector.should_split([]) == []


def test_should_split_fallback_on_exception() -> None:
    detector = SemanticBoundaryDetector(FailingLLM())
    turns = [Turn("user", "hello", "2026-04-14T10:00:00Z")]
    assert detector.should_split(turns) == []


def test_should_split_fallback_on_long_turns() -> None:
    detector = SemanticBoundaryDetector(MockLLM('{"split_indices": []}'))
    turns = [Turn("user", "x" * 9000, "2026-04-14T10:00:00Z")]
    assert detector.should_split(turns) == [1]


def test_parse_split_indices_from_dict() -> None:
    assert _parse_split_indices('{"split_indices": [3, 6]}', 8) == [3, 6]


def test_parse_split_indices_from_list() -> None:
    assert _parse_split_indices("[3, 6]", 8) == [3, 6]


def test_parse_split_indices_deduplicates_and_sorts() -> None:
    assert _parse_split_indices('{"split_indices": [6, 3, 3, 9]}', 10) == [3, 6, 9]


def test_parse_split_indices_filters_invalid() -> None:
    assert _parse_split_indices('{"split_indices": [0, -1, 3, 100]}', 5) == [3]


def test_parse_split_indices_empty() -> None:
    assert _parse_split_indices('{"split_indices": []}', 5) == []
    assert _parse_split_indices("{}", 5) == []
    assert _parse_split_indices("not json", 5) == []
