from __future__ import annotations


from session_mem.core.buffer import Turn
from session_mem.core.boundary_detector import SemanticBoundaryDetector
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


def test_should_split_returns_true_on_split() -> None:
    detector = SemanticBoundaryDetector(MockLLM("SPLIT"))
    turns = [Turn("user", "hello", "2026-04-14T10:00:00Z")]
    assert detector.should_split(turns)


def test_should_split_returns_false_on_continue() -> None:
    detector = SemanticBoundaryDetector(MockLLM("CONTINUE"))
    turns = [Turn("user", "hello", "2026-04-14T10:00:00Z")]
    assert not detector.should_split(turns)


def test_should_split_empty_turns() -> None:
    detector = SemanticBoundaryDetector(MockLLM("SPLIT"))
    assert not detector.should_split([])


def test_should_split_fallback_on_exception() -> None:
    detector = SemanticBoundaryDetector(FailingLLM())
    turns = [Turn("user", "hello", "2026-04-14T10:00:00Z")]
    assert not detector.should_split(turns)


def test_should_split_fallback_on_long_turns() -> None:
    detector = SemanticBoundaryDetector(MockLLM("CONTINUE"))
    turns = [Turn("user", "x" * 9000, "2026-04-14T10:00:00Z")]
    assert detector.should_split(turns)
