from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class QAMetrics:
    """单个 QA 的评估指标（三种方式对比）。"""

    session_id: str
    question_id: int
    question: str
    ground_truth: str

    # 全量基线
    baseline_tokens: int = 0
    baseline_latency_ms: float = 0.0
    baseline_answer: str = ""

    # 滑窗基线
    sliding_tokens: int = 0
    sliding_latency_ms: float = 0.0
    sliding_answer: str = ""

    # session-mem
    session_mem_tokens: int = 0
    session_mem_latency_ms: float = 0.0
    session_mem_answer: str = ""

    # Token 节省率
    token_saving_rate_vs_baseline: float = 0.0
    token_saving_rate_vs_sliding: float = 0.0

    # Judge 评分（session-mem vs 全量 / vs 滑窗）
    judge_score_vs_baseline: float | None = None
    judge_score_vs_sliding: float | None = None


@dataclass
class EvaluationResult:
    """整个评估集的综合结果。"""

    total_qas: int = 0

    # Token 节省率
    avg_token_saving_rate_vs_baseline: float = 0.0
    avg_token_saving_rate_vs_sliding: float = 0.0

    # 延迟
    avg_baseline_latency_ms: float = 0.0
    avg_sliding_latency_ms: float = 0.0
    avg_session_mem_latency_ms: float = 0.0
    median_session_mem_latency_ms: float = 0.0
    p95_session_mem_latency_ms: float = 0.0

    # Judge
    avg_judge_score_vs_baseline: float | None = None
    avg_judge_score_vs_sliding: float | None = None

    qas: list[QAMetrics] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_qas": self.total_qas,
            "avg_token_saving_rate_vs_baseline": self.avg_token_saving_rate_vs_baseline,
            "avg_token_saving_rate_vs_sliding": self.avg_token_saving_rate_vs_sliding,
            "avg_baseline_latency_ms": self.avg_baseline_latency_ms,
            "avg_sliding_latency_ms": self.avg_sliding_latency_ms,
            "avg_session_mem_latency_ms": self.avg_session_mem_latency_ms,
            "median_session_mem_latency_ms": self.median_session_mem_latency_ms,
            "p95_session_mem_latency_ms": self.p95_session_mem_latency_ms,
            "avg_judge_score_vs_baseline": self.avg_judge_score_vs_baseline,
            "avg_judge_score_vs_sliding": self.avg_judge_score_vs_sliding,
            "qas": [
                {
                    "session_id": q.session_id,
                    "question_id": q.question_id,
                    "question": q.question,
                    "ground_truth": q.ground_truth,
                    "baseline_tokens": q.baseline_tokens,
                    "baseline_latency_ms": q.baseline_latency_ms,
                    "baseline_answer": q.baseline_answer,
                    "sliding_tokens": q.sliding_tokens,
                    "sliding_latency_ms": q.sliding_latency_ms,
                    "sliding_answer": q.sliding_answer,
                    "session_mem_tokens": q.session_mem_tokens,
                    "session_mem_latency_ms": q.session_mem_latency_ms,
                    "session_mem_answer": q.session_mem_answer,
                    "token_saving_rate_vs_baseline": q.token_saving_rate_vs_baseline,
                    "token_saving_rate_vs_sliding": q.token_saving_rate_vs_sliding,
                    "judge_score_vs_baseline": q.judge_score_vs_baseline,
                    "judge_score_vs_sliding": q.judge_score_vs_sliding,
                }
                for q in self.qas
            ],
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )


def compute_aggregate(qas: list[QAMetrics]) -> EvaluationResult:
    """从单个 QA 指标计算综合结果。"""
    if not qas:
        return EvaluationResult()

    total = len(qas)

    avg_save_base = sum(q.token_saving_rate_vs_baseline for q in qas) / total
    avg_save_slide = sum(q.token_saving_rate_vs_sliding for q in qas) / total

    base_lats = [q.baseline_latency_ms for q in qas]
    slide_lats = [q.sliding_latency_ms for q in qas]
    sm_lats = sorted(q.session_mem_latency_ms for q in qas)

    avg_base_lat = sum(base_lats) / total
    avg_slide_lat = sum(slide_lats) / total
    avg_sm_lat = sum(sm_lats) / total
    median_sm_lat = (
        sm_lats[total // 2]
        if total % 2 == 1
        else (sm_lats[total // 2 - 1] + sm_lats[total // 2]) / 2
    )
    p95_idx = int(total * 0.95)
    p95_sm_lat = sm_lats[min(p95_idx, total - 1)]

    judge_base = [q.judge_score_vs_baseline for q in qas if q.judge_score_vs_baseline is not None]
    judge_slide = [q.judge_score_vs_sliding for q in qas if q.judge_score_vs_sliding is not None]

    avg_judge_base = sum(judge_base) / len(judge_base) if judge_base else None
    avg_judge_slide = sum(judge_slide) / len(judge_slide) if judge_slide else None

    return EvaluationResult(
        total_qas=total,
        avg_token_saving_rate_vs_baseline=avg_save_base,
        avg_token_saving_rate_vs_sliding=avg_save_slide,
        avg_baseline_latency_ms=avg_base_lat,
        avg_sliding_latency_ms=avg_slide_lat,
        avg_session_mem_latency_ms=avg_sm_lat,
        median_session_mem_latency_ms=median_sm_lat,
        p95_session_mem_latency_ms=p95_sm_lat,
        avg_judge_score_vs_baseline=avg_judge_base,
        avg_judge_score_vs_sliding=avg_judge_slide,
        qas=qas,
    )


def judge_answer(
    question: str,
    ground_truth: str,
    candidate_answer: str,
    judge_client,
    judge_model: str = "gpt-4o-mini",
) -> float:
    """
    使用 LLM-as-Judge 评估候选回答与标准答案的一致性。

    返回 0-1 的相似度/正确性分数，1.0 表示完全一致/正确。
    """
    system_prompt = (
        "你是一个严格的答案评判助手。请根据问题和标准答案，评估候选回答的质量。"
        "只输出一个 0 到 1 之间的数字，1 表示与标准答案完全一致且正确，0 表示完全错误。"
        "不要输出任何解释。"
    )
    user_prompt = (
        f"问题：{question}\n\n"
        f"标准答案：{ground_truth}\n\n"
        f"候选回答：{candidate_answer}\n\n"
        "请输出一个 0-1 的数字："
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
        match = re.search(r"[\d.]+", score_text)
        if match:
            score = float(match.group())
            return max(0.0, min(1.0, score))
    except Exception:
        pass
    return 0.0
