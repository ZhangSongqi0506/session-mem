from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SessionMetrics:
    """单条 session 的评估指标。"""

    session_id: str
    baseline_tokens: int = 0
    session_mem_tokens: int = 0
    token_saving_rate: float = 0.0
    retrieve_latency_ms: float = 0.0
    baseline_answer: str = ""
    session_mem_answer: str = ""
    judge_score: float | None = None


@dataclass
class EvaluationResult:
    """整个评估集的综合结果。"""

    avg_token_saving_rate: float = 0.0
    avg_retrieve_latency_ms: float = 0.0
    median_retrieve_latency_ms: float = 0.0
    p95_retrieve_latency_ms: float = 0.0
    avg_judge_score: float | None = None
    total_sessions: int = 0
    sessions: list[SessionMetrics] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "avg_token_saving_rate": self.avg_token_saving_rate,
            "avg_retrieve_latency_ms": self.avg_retrieve_latency_ms,
            "median_retrieve_latency_ms": self.median_retrieve_latency_ms,
            "p95_retrieve_latency_ms": self.p95_retrieve_latency_ms,
            "avg_judge_score": self.avg_judge_score,
            "total_sessions": self.total_sessions,
            "sessions": [
                {
                    "session_id": s.session_id,
                    "baseline_tokens": s.baseline_tokens,
                    "session_mem_tokens": s.session_mem_tokens,
                    "token_saving_rate": s.token_saving_rate,
                    "retrieve_latency_ms": s.retrieve_latency_ms,
                    "baseline_answer": s.baseline_answer,
                    "session_mem_answer": s.session_mem_answer,
                    "judge_score": s.judge_score,
                }
                for s in self.sessions
            ],
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )


def compute_aggregate(sessions: list[SessionMetrics]) -> EvaluationResult:
    """从单条 session 指标计算综合结果。"""
    if not sessions:
        return EvaluationResult()

    total = len(sessions)
    avg_save = sum(s.token_saving_rate for s in sessions) / total
    latencies = sorted(s.retrieve_latency_ms for s in sessions)
    avg_lat = sum(latencies) / total
    median_lat = (
        latencies[total // 2]
        if total % 2 == 1
        else (latencies[total // 2 - 1] + latencies[total // 2]) / 2
    )
    p95_idx = int(total * 0.95)
    p95_lat = latencies[min(p95_idx, total - 1)]

    judge_scores = [s.judge_score for s in sessions if s.judge_score is not None]
    avg_judge = sum(judge_scores) / len(judge_scores) if judge_scores else None

    return EvaluationResult(
        avg_token_saving_rate=avg_save,
        avg_retrieve_latency_ms=avg_lat,
        median_retrieve_latency_ms=median_lat,
        p95_retrieve_latency_ms=p95_lat,
        avg_judge_score=avg_judge,
        total_sessions=total,
        sessions=sessions,
    )


def judge_answer(
    question: str,
    ground_truth: str,
    baseline_answer: str,
    session_mem_answer: str,
    judge_client,
    judge_model: str = "gpt-4o-mini",
) -> float:
    """
    使用 LLM-as-Judge 对比 baseline 回答与 session-mem 回答。

    返回 0-1 的相似度/正确性分数，1.0 表示完全一致/正确。
    """
    system_prompt = (
        "你是一个严格的答案评判助手。请根据问题和标准答案，评估两个模型回答的质量。"
        "只输出一个 0 到 1 之间的数字，1 表示与标准答案完全一致且正确，0 表示完全错误。"
        "不要输出任何解释。"
    )
    user_prompt = (
        f"问题：{question}\n\n"
        f"标准答案：{ground_truth}\n\n"
        f"回答 A：{baseline_answer}\n\n"
        f"回答 B：{session_mem_answer}\n\n"
        "请判断回答 B 相对于回答 A，在准确性和完整性上是否等价。输出一个 0-1 的数字："
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        resp = judge_client.chat_completion(
            messages=messages,
            model=judge_model,
            temperature=0.0,
        )
        score_text = resp.strip()
        # 尝试提取第一个数字
        import re

        match = re.search(r"[\d.]+", score_text)
        if match:
            score = float(match.group())
            return max(0.0, min(1.0, score))
    except Exception:
        pass
    return 0.0
