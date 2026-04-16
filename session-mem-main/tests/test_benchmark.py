from __future__ import annotations

import json
import tempfile
from pathlib import Path

from benchmarks.data_loader import load_locomo_sessions
from benchmarks.locomo_runner import run_session
from benchmarks.metrics import QAMetrics, compute_aggregate, judge_answer
from benchmarks.prompt_assembler import PromptAssembler


class FakeLLMClient:
    def __init__(self, response: str = ""):
        self.response = response

    def chat_completion(self, messages, temperature=0.3, **kwargs):
        return self.response

    def isolated_chat(self, messages, temperature=0.3, response_format=None, **kwargs):
        return self.response

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] + [0.0] * 1022]


# -----------------------------------------------------------------------------
# data_loader tests
# -----------------------------------------------------------------------------


def test_load_locomo_sessions_merges_sessions():
    raw_data = [
        {
            "sample_id": "conv-test",
            "conversation": {
                "speaker_a": "Alice",
                "speaker_b": "Bob",
                "session_1_date_time": "1 Jan 2023",
                "session_1": [
                    {"speaker": "Alice", "dia_id": "D1:1", "text": "Hello"},
                    {"speaker": "Bob", "dia_id": "D1:2", "text": "Hi"},
                ],
                "session_2_date_time": "2 Jan 2023",
                "session_2": [
                    {"speaker": "Alice", "dia_id": "D2:1", "text": "How are you?"},
                ],
            },
            "qa": [{"question": "Who said hello?", "answer": "Alice", "evidence": ["D1:1"]}],
        }
    ]

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as f:
        json.dump(raw_data, f)
        path = f.name

    sessions = load_locomo_sessions(path)
    assert len(sessions) == 1
    assert sessions[0].session_id == "conv-test"
    assert sessions[0].turn_count == 3
    assert sessions[0].turns[0]["role"] == "Alice"
    assert sessions[0].turns[1]["role"] == "Bob"
    assert sessions[0].qa_list[0]["question"] == "Who said hello?"

    Path(path).unlink()


def test_load_locomo_sessions_respects_max_sessions_and_max_turns():
    raw_data = [
        {
            "sample_id": "conv-1",
            "conversation": {
                "speaker_a": "A",
                "speaker_b": "B",
                "session_1": [
                    {"speaker": "A", "dia_id": "D1:1", "text": "t1"},
                    {"speaker": "B", "dia_id": "D1:2", "text": "t2"},
                    {"speaker": "A", "dia_id": "D1:3", "text": "t3"},
                ],
            },
            "qa": [],
        },
        {
            "sample_id": "conv-2",
            "conversation": {
                "speaker_a": "A",
                "speaker_b": "B",
                "session_1": [
                    {"speaker": "A", "dia_id": "D1:1", "text": "x1"},
                ],
            },
            "qa": [],
        },
    ]

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as f:
        json.dump(raw_data, f)
        path = f.name

    sessions = load_locomo_sessions(path, max_sessions=1, max_turns=2)
    assert len(sessions) == 1
    assert sessions[0].turn_count == 2

    Path(path).unlink()


# -----------------------------------------------------------------------------
# prompt_assembler tests
# -----------------------------------------------------------------------------


def test_prompt_assembler_baseline_includes_all_turns():
    assembler = PromptAssembler(token_estimator=lambda x: len(x))
    turns = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    msgs, tokens = assembler.build_baseline(turns, query="q")
    assert tokens == len("[User]: a\n\n[Assistant]: b\n\n[User]: c\n\n[User]: q")
    assert "[User]: a" in msgs[0]["content"]
    assert "[User]: q" in msgs[0]["content"]


def test_prompt_assembler_sliding_window_keeps_last_n():
    assembler = PromptAssembler(token_estimator=lambda x: len(x))
    turns = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
    ]
    msgs, tokens = assembler.build_sliding_window(turns, query="q", window_size=2)
    content = msgs[0]["content"]
    assert "[User]: c" in content
    assert "[Assistant]: d" in content
    assert "[User]: a" not in content
    assert "[Assistant]: b" not in content
    assert "[User]: q" in content


# -----------------------------------------------------------------------------
# locomo_runner integration tests (lightweight, fake LLM)
# -----------------------------------------------------------------------------


def test_run_session_produces_three_way_comparison():
    from benchmarks.data_loader import LoCoMoSession

    session = LoCoMoSession(
        session_id="s1",
        turns=[
            {"role": "user", "content": "My name is Alice.", "timestamp": "2023-01-01T00:00:00Z"},
            {
                "role": "assistant",
                "content": "Nice to meet you Alice.",
                "timestamp": "2023-01-01T00:01:00Z",
            },
            {"role": "user", "content": "I live in Paris.", "timestamp": "2023-01-01T00:02:00Z"},
            {
                "role": "assistant",
                "content": "Paris is beautiful.",
                "timestamp": "2023-01-01T00:03:00Z",
            },
        ],
        qa_list=[
            {"question": "Where do I live?", "answer": "Paris", "evidence": ["D1:3"]},
        ],
    )

    llm = FakeLLMClient(response="Paris")
    qas = run_session(
        session,
        llm_client=llm,
        judge_client=None,
        run_accuracy=False,
        sliding_window_size=2,
    )

    assert len(qas) == 1
    m = qas[0]
    assert m.session_id == "s1"
    assert m.question == "Where do I live?"

    # baseline 应包含全部 4 轮 + query
    assert m.baseline_tokens > 0
    # sliding 只含最后 2 轮 + query
    assert m.sliding_tokens > 0
    assert m.sliding_tokens < m.baseline_tokens
    # session-mem 有 meta cell + hot zone + 可能激活 cell
    assert m.session_mem_tokens >= 0

    # token 节省率
    assert (
        m.token_saving_rate_vs_baseline
        == (m.baseline_tokens - m.session_mem_tokens) / m.baseline_tokens
    )
    assert (
        m.token_saving_rate_vs_sliding
        == (m.sliding_tokens - m.session_mem_tokens) / m.sliding_tokens
    )

    # latency：未跑 accuracy 时应为 0
    assert m.baseline_latency_ms == 0.0
    assert m.sliding_latency_ms == 0.0
    assert m.session_mem_latency_ms >= 0.0


def test_run_session_accuracy_mode_populates_answers():
    from benchmarks.data_loader import LoCoMoSession

    session = LoCoMoSession(
        session_id="s1",
        turns=[
            {"role": "user", "content": "Budget is 10k.", "timestamp": "2023-01-01T00:00:00Z"},
            {"role": "assistant", "content": "Got it.", "timestamp": "2023-01-01T00:01:00Z"},
        ],
        qa_list=[
            {"question": "What is the budget?", "answer": "10k", "evidence": ["D1:1"]},
        ],
    )

    llm = FakeLLMClient(response="10k")
    qas = run_session(
        session,
        llm_client=llm,
        judge_client=llm,
        run_accuracy=True,
        sliding_window_size=10,
    )

    assert len(qas) == 1
    m = qas[0]
    assert m.baseline_answer == "10k"
    assert m.sliding_answer == "10k"
    assert m.session_mem_answer == "10k"
    # latency 在有 accuracy 时应 > 0（虽然是 fake，但有时间开销）
    assert m.baseline_latency_ms >= 0.0
    assert m.sliding_latency_ms >= 0.0


# -----------------------------------------------------------------------------
# metrics tests
# -----------------------------------------------------------------------------


def test_compute_aggregate_empty():
    agg = compute_aggregate([])
    assert agg.total_qas == 0


def test_compute_aggregate_with_qas():
    qas = [
        QAMetrics(
            session_id="s1",
            question_id=0,
            question="q1",
            ground_truth="a1",
            baseline_tokens=100,
            session_mem_tokens=50,
            token_saving_rate_vs_baseline=0.5,
            token_saving_rate_vs_sliding=0.2,
            session_mem_latency_ms=10.0,
            baseline_latency_ms=5.0,
            sliding_latency_ms=3.0,
            baseline_judge_score=0.8,
            sliding_judge_score=0.6,
            session_mem_judge_score=0.9,
            session_mem_internal_tokens=12,
        ),
        QAMetrics(
            session_id="s1",
            question_id=1,
            question="q2",
            ground_truth="a2",
            baseline_tokens=200,
            session_mem_tokens=80,
            token_saving_rate_vs_baseline=0.6,
            token_saving_rate_vs_sliding=0.3,
            session_mem_latency_ms=20.0,
            baseline_latency_ms=6.0,
            sliding_latency_ms=4.0,
            baseline_judge_score=0.7,
            sliding_judge_score=0.5,
            session_mem_judge_score=0.85,
            session_mem_internal_tokens=8,
        ),
    ]
    agg = compute_aggregate(qas)
    assert agg.total_qas == 2
    assert agg.avg_token_saving_rate_vs_baseline == 0.55
    assert agg.avg_token_saving_rate_vs_sliding == 0.25
    assert agg.avg_session_mem_latency_ms == 15.0
    assert agg.median_session_mem_latency_ms == 15.0
    assert agg.avg_session_mem_internal_tokens == 10.0
    assert agg.avg_baseline_judge_score == 0.75
    assert agg.avg_sliding_judge_score == 0.55
    assert agg.avg_session_mem_judge_score == 0.875


def test_judge_answer_extracts_score():
    class FakeJudge:
        def chat_completion(self, messages, model, temperature):
            return "0.85"

    score = judge_answer("q", "a", "ans", FakeJudge())
    assert score == 0.85


def test_judge_answer_clamps_out_of_range():
    class FakeJudge:
        def chat_completion(self, messages, model, temperature):
            return "1.5"

    score = judge_answer("q", "a", "ans", FakeJudge())
    assert score == 1.0


def test_judge_answer_fallback_on_failure():
    class FakeJudge:
        def chat_completion(self, messages, model, temperature):
            raise RuntimeError("fail")

    score = judge_answer("q", "a", "ans", FakeJudge())
    assert score == 0.0


def test_judge_answer_logs_exception_on_failure(caplog):
    import logging

    class FakeJudge:
        def chat_completion(self, messages, model, temperature):
            raise RuntimeError("network down")

    with caplog.at_level(logging.WARNING, logger="benchmarks.metrics"):
        score = judge_answer("q", "a", "ans", FakeJudge())

    assert score == 0.0
    assert "network down" in caplog.text


def test_evaluation_result_text_report():
    qas = [
        QAMetrics(
            session_id="s1",
            question_id=0,
            question="q1",
            ground_truth="a1",
            baseline_tokens=100,
            session_mem_tokens=50,
            token_saving_rate_vs_baseline=0.5,
            token_saving_rate_vs_sliding=0.2,
            session_mem_latency_ms=10.0,
            session_mem_meta_cell_tokens=5,
            session_mem_hot_zone_tokens=10,
            session_mem_activated_cell_count=1,
            session_mem_activated_cells=[
                {
                    "cell_id": "C_001",
                    "cell_type": "fact",
                    "summary": "test summary",
                    "token_count": 35,
                }
            ],
            session_mem_internal_tokens=7,
            baseline_judge_score=0.6,
            sliding_judge_score=0.7,
            session_mem_judge_score=0.8,
        ),
    ]
    agg = compute_aggregate(qas)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        path = f.name

    agg.save_text_report(path)
    text = Path(path).read_text(encoding="utf-8")
    assert "Session-mem LoCoMo Evaluation Report" in text
    assert "Meta Cell: 5 tokens" in text
    assert "Hot Zone: 10 tokens" in text
    assert "Internal Tokens (retrieval): 7 tokens" in text
    assert "C_001 [fact] 35 tokens: test summary" in text
    assert "Judge: 0.6" in text
    assert "Judge: 0.7" in text
    assert "Judge: 0.8" in text
    assert "Avg Baseline Judge: 0.600" in text
    assert "Avg Sliding Judge: 0.700" in text
    assert "Avg session-mem Judge: 0.800" in text

    Path(path).unlink()


def test_run_session_detail_fields_exist():
    from benchmarks.data_loader import LoCoMoSession

    session = LoCoMoSession(
        session_id="s1",
        turns=[
            {"role": "user", "content": "My name is Alice.", "timestamp": "2023-01-01T00:00:00Z"},
            {
                "role": "assistant",
                "content": "Nice to meet you Alice.",
                "timestamp": "2023-01-01T00:01:00Z",
            },
        ],
        qa_list=[
            {"question": "What is my name?", "answer": "Alice", "evidence": ["D1:1"]},
        ],
    )

    llm = FakeLLMClient(response="Alice")
    qas = run_session(
        session,
        llm_client=llm,
        judge_client=None,
        run_accuracy=False,
        sliding_window_size=10,
    )

    assert len(qas) == 1
    m = qas[0]
    # 新字段应存在（即使值为 0）
    assert hasattr(m, "session_mem_meta_cell_tokens")
    assert hasattr(m, "session_mem_hot_zone_tokens")
    assert hasattr(m, "session_mem_activated_cell_count")
    assert hasattr(m, "session_mem_activated_cells")
    assert hasattr(m, "session_mem_internal_tokens")
    assert isinstance(m.session_mem_activated_cells, list)


def test_judge_all_three_answers():
    call_log = []

    class FakeJudge:
        def chat_completion(self, messages, model, temperature):
            call_log.append(messages[-1]["content"])
            return "0.75"

    q = QAMetrics(
        session_id="s1",
        question_id=0,
        question="q",
        ground_truth="gt",
        baseline_answer="base ans",
        sliding_answer="slide ans",
        session_mem_answer="sm ans",
    )

    q.baseline_judge_score = judge_answer(
        q.question, q.ground_truth, q.baseline_answer, FakeJudge()
    )
    q.sliding_judge_score = judge_answer(q.question, q.ground_truth, q.sliding_answer, FakeJudge())
    q.session_mem_judge_score = judge_answer(
        q.question, q.ground_truth, q.session_mem_answer, FakeJudge()
    )

    assert q.baseline_judge_score == 0.75
    assert q.sliding_judge_score == 0.75
    assert q.session_mem_judge_score == 0.75
    assert len(call_log) == 3
    assert "base ans" in call_log[0]
    assert "slide ans" in call_log[1]
    assert "sm ans" in call_log[2]
