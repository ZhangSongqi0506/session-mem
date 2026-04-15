from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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

    # session-mem Token 拆解
    session_mem_meta_cell_tokens: int = 0
    session_mem_hot_zone_tokens: int = 0
    session_mem_activated_cell_count: int = 0
    session_mem_activated_cells: list[dict[str, Any]] = field(default_factory=list)

    # Token 节省率
    token_saving_rate_vs_baseline: float = 0.0
    token_saving_rate_vs_sliding: float = 0.0

    # Judge 评分（session-mem vs 全量 / vs 滑窗）
    judge_score_vs_baseline: float | None = None
    judge_score_vs_sliding: float | None = None

    # Judge 评分（各回答各自 vs ground_truth）
    baseline_judge_score: float | None = None
    sliding_judge_score: float | None = None
    session_mem_judge_score: float | None = None


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
    avg_baseline_judge_score: float | None = None
    avg_sliding_judge_score: float | None = None
    avg_session_mem_judge_score: float | None = None

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
            "avg_baseline_judge_score": self.avg_baseline_judge_score,
            "avg_sliding_judge_score": self.avg_sliding_judge_score,
            "avg_session_mem_judge_score": self.avg_session_mem_judge_score,
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
                    "session_mem_meta_cell_tokens": q.session_mem_meta_cell_tokens,
                    "session_mem_hot_zone_tokens": q.session_mem_hot_zone_tokens,
                    "session_mem_activated_cell_count": q.session_mem_activated_cell_count,
                    "session_mem_activated_cells": q.session_mem_activated_cells,
                    "token_saving_rate_vs_baseline": q.token_saving_rate_vs_baseline,
                    "token_saving_rate_vs_sliding": q.token_saving_rate_vs_sliding,
                    "judge_score_vs_baseline": q.judge_score_vs_baseline,
                    "judge_score_vs_sliding": q.judge_score_vs_sliding,
                    "baseline_judge_score": q.baseline_judge_score,
                    "sliding_judge_score": q.sliding_judge_score,
                    "session_mem_judge_score": q.session_mem_judge_score,
                }
                for q in self.qas
            ],
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_text_report(self, path: str | Path) -> None:
        """生成可读的 per-QA 文本报告。"""
        lines: list[str] = []
        lines.append("=" * 70)
        lines.append("Session-mem LoCoMo Evaluation Report")
        lines.append(f"Total QAs: {self.total_qas}")
        lines.append(
            f"Avg Token Saving vs Baseline: {self.avg_token_saving_rate_vs_baseline * 100:.2f}%"
        )
        lines.append(
            f"Avg Token Saving vs Sliding: {self.avg_token_saving_rate_vs_sliding * 100:.2f}%"
        )
        lines.append(f"Avg session-mem Latency: {self.avg_session_mem_latency_ms:.2f} ms")
        if self.avg_baseline_judge_score is not None:
            lines.append(f"Avg Baseline Judge: {self.avg_baseline_judge_score:.3f}")
        if self.avg_sliding_judge_score is not None:
            lines.append(f"Avg Sliding Judge: {self.avg_sliding_judge_score:.3f}")
        if self.avg_session_mem_judge_score is not None:
            lines.append(f"Avg session-mem Judge: {self.avg_session_mem_judge_score:.3f}")
        lines.append("=" * 70)
        lines.append("")

        for idx, q in enumerate(self.qas, start=1):
            lines.append(f"--- QA {idx}/{self.total_qas} | {q.session_id} ---")
            lines.append(f"Question: {q.question}")
            lines.append(f"Ground Truth: {q.ground_truth}")
            lines.append(f"Token Saving vs Baseline: {q.token_saving_rate_vs_baseline * 100:.2f}%")
            lines.append(f"Token Saving vs Sliding: {q.token_saving_rate_vs_sliding * 100:.2f}%")
            lines.append("")

            lines.append("[Baseline]")
            lines.append(f"  Tokens: {q.baseline_tokens} | Judge: {q.baseline_judge_score}")
            lines.append(f"  Answer: {q.baseline_answer or '(empty)'}")
            lines.append("")

            lines.append("[Sliding]")
            lines.append(f"  Tokens: {q.sliding_tokens} | Judge: {q.sliding_judge_score}")
            lines.append(f"  Answer: {q.sliding_answer or '(empty)'}")
            lines.append("")

            lines.append("[session-mem]")
            lines.append(f"  Tokens: {q.session_mem_tokens} | Judge: {q.session_mem_judge_score}")
            lines.append(f"  Meta Cell: {q.session_mem_meta_cell_tokens} tokens")
            lines.append(f"  Hot Zone: {q.session_mem_hot_zone_tokens} tokens")
            lines.append(f"  Activated Cells ({q.session_mem_activated_cell_count}):")
            for cell in q.session_mem_activated_cells:
                lines.append(
                    f"    - {cell.get('cell_id')} [{cell.get('cell_type')}] "
                    f"{cell.get('token_count')} tokens: {cell.get('summary', '')}"
                )
            lines.append(f"  Answer: {q.session_mem_answer or '(empty)'}")
            lines.append("")
            lines.append("=" * 70)
            lines.append("")

        Path(path).write_text("\n".join(lines), encoding="utf-8")


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

    baseline_judge_scores = [
        q.baseline_judge_score for q in qas if q.baseline_judge_score is not None
    ]
    sliding_judge_scores = [q.sliding_judge_score for q in qas if q.sliding_judge_score is not None]
    session_mem_judge_scores = [
        q.session_mem_judge_score for q in qas if q.session_mem_judge_score is not None
    ]

    avg_baseline_judge = (
        sum(baseline_judge_scores) / len(baseline_judge_scores) if baseline_judge_scores else None
    )
    avg_sliding_judge = (
        sum(sliding_judge_scores) / len(sliding_judge_scores) if sliding_judge_scores else None
    )
    avg_session_mem_judge = (
        sum(session_mem_judge_scores) / len(session_mem_judge_scores)
        if session_mem_judge_scores
        else None
    )

    return EvaluationResult(
        total_qas=total,
        avg_token_saving_rate_vs_baseline=avg_save_base,
        avg_token_saving_rate_vs_sliding=avg_save_slide,
        avg_baseline_latency_ms=avg_base_lat,
        avg_sliding_latency_ms=avg_slide_lat,
        avg_session_mem_latency_ms=avg_sm_lat,
        median_session_mem_latency_ms=median_sm_lat,
        p95_session_mem_latency_ms=p95_sm_lat,
        avg_baseline_judge_score=avg_baseline_judge,
        avg_sliding_judge_score=avg_sliding_judge,
        avg_session_mem_judge_score=avg_session_mem_judge,
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
    except Exception as exc:
        logger.warning("Judge evaluation failed: %s", exc)
    return 0.0
