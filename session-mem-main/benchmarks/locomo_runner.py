"""LoCoMo benchmark runner for session-mem."""

from __future__ import annotations

import argparse
import logging
import tempfile
import time
from pathlib import Path

from session_mem.core.memory_system import MemorySystem
from session_mem.llm.qwen_client import QwenClient
from session_mem.storage.sqlite_backend import SQLiteBackend
from session_mem.utils.tokenizer import TokenEstimator

from benchmarks.data_loader import load_locomo_sessions
from benchmarks.metrics import QAMetrics, compute_aggregate, judge_answer
from benchmarks.prompt_assembler import PromptAssembler

logger = logging.getLogger(__name__)


def _answer(llm_client, messages: list[dict[str, str]]) -> str:
    """调用 LLM 生成回答。"""
    if not messages:
        return ""
    try:
        resp = llm_client.chat_completion(messages=messages, temperature=0.3)
        return resp.strip()
    except Exception as exc:
        logger.warning("LLM answer failed: %s", exc)
        return ""


def run_session(
    session,
    llm_client: QwenClient,
    judge_client: QwenClient | None,
    run_accuracy: bool,
    sliding_window_size: int,
    db_path: str | None = None,
) -> list[QAMetrics]:
    """评估单条合并会话：先全量写入 MemorySystem，再对每个 QA 跑三种方式对比。"""
    assembler = PromptAssembler()

    # 1. 初始化 MemorySystem 并逐轮写入
    if db_path is None:
        db_fd = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = db_fd.name
        db_fd.close()

    backend = SQLiteBackend(db_path)
    ms = MemorySystem(
        session_id=session.session_id,
        llm_client=llm_client,
        vector_index=backend.vector_index,
        cell_store=backend.cell_store,
        text_store=backend.text_store,
        meta_cell_store=backend,
        embedding_client=llm_client,
    )

    for turn in session.turns:
        ms.add_turn(
            role=turn["role"],
            content=turn["content"],
            timestamp=turn["timestamp"],
        )

    # 2. 对每个 QA 进行评估
    results: list[QAMetrics] = []
    for qa_idx, qa in enumerate(session.qa_list):
        question = qa.get("question", "")
        ground_truth = str(qa.get("answer", ""))
        if not question:
            continue

        metrics = QAMetrics(
            session_id=session.session_id,
            question_id=qa_idx,
            question=question,
            ground_truth=ground_truth,
        )

        # --- 全量基线 ---
        baseline_msgs, baseline_tokens = assembler.build_baseline(session.turns, query=question)
        metrics.baseline_tokens = baseline_tokens

        if run_accuracy:
            start = time.perf_counter()
            metrics.baseline_answer = _answer(llm_client, baseline_msgs)
            metrics.baseline_latency_ms = (time.perf_counter() - start) * 1000
        else:
            # 不跑 LLM 时 latency 用 token 估算作为代理（或设为 0）
            metrics.baseline_latency_ms = 0.0

        # --- 滑窗基线 ---
        slide_msgs, slide_tokens = assembler.build_sliding_window(
            session.turns, query=question, window_size=sliding_window_size
        )
        metrics.sliding_tokens = slide_tokens

        if run_accuracy:
            start = time.perf_counter()
            metrics.sliding_answer = _answer(llm_client, slide_msgs)
            metrics.sliding_latency_ms = (time.perf_counter() - start) * 1000
        else:
            metrics.sliding_latency_ms = 0.0

        # --- session-mem ---
        start = time.perf_counter()
        wm = ms.retrieve_context(question, hot_zone_turns=2, top_k=2)
        metrics.session_mem_latency_ms = (time.perf_counter() - start) * 1000

        sm_prompt = wm.to_prompt()
        sm_msgs = list(sm_prompt)
        if sm_msgs:
            sm_msgs.append({"role": "user", "content": question})
        else:
            sm_msgs = [{"role": "user", "content": question}]

        content = sm_msgs[0]["content"] if sm_msgs else ""
        metrics.session_mem_tokens = TokenEstimator().estimate(content)

        if run_accuracy:
            metrics.session_mem_answer = _answer(llm_client, sm_msgs)

        # Token 节省率
        if metrics.baseline_tokens > 0:
            metrics.token_saving_rate_vs_baseline = (
                metrics.baseline_tokens - metrics.session_mem_tokens
            ) / metrics.baseline_tokens
        if metrics.sliding_tokens > 0:
            metrics.token_saving_rate_vs_sliding = (
                metrics.sliding_tokens - metrics.session_mem_tokens
            ) / metrics.sliding_tokens

        # Judge 评分
        if run_accuracy and judge_client and metrics.session_mem_answer:
            if metrics.baseline_answer:
                try:
                    metrics.judge_score_vs_baseline = judge_answer(
                        question=question,
                        ground_truth=ground_truth,
                        candidate_answer=metrics.session_mem_answer,
                        judge_client=judge_client,
                    )
                except Exception as exc:
                    logger.warning("Judge vs baseline failed: %s", exc)

            if metrics.sliding_answer:
                try:
                    metrics.judge_score_vs_sliding = judge_answer(
                        question=question,
                        ground_truth=ground_truth,
                        candidate_answer=metrics.session_mem_answer,
                        judge_client=judge_client,
                    )
                except Exception as exc:
                    logger.warning("Judge vs sliding failed: %s", exc)

        results.append(metrics)

    backend.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="LoCoMo benchmark for session-mem")
    parser.add_argument("--data_path", required=True, help="Path to LoCoMo JSON file")
    parser.add_argument(
        "--max_sessions", type=int, default=None, help="Max conversations to evaluate"
    )
    parser.add_argument(
        "--max_turns", type=int, default=None, help="Max turns per merged conversation"
    )
    parser.add_argument(
        "--max_qa_per_session", type=int, default=None, help="Max QA per conversation"
    )
    parser.add_argument(
        "--output", default="benchmarks/results/locomo_results.json", help="Output JSON path"
    )
    parser.add_argument(
        "--run_accuracy", action="store_true", help="Run LLM answer + judge evaluation"
    )
    parser.add_argument(
        "--skip_judge",
        action="store_true",
        help="Skip judge scoring even when --run_accuracy is set (only generate answers)",
    )
    parser.add_argument(
        "--sliding_window", type=int, default=10, help="Sliding window size (turns)"
    )
    parser.add_argument(
        "--llm_base_url", default="http://172.10.10.200/v1", help="Main LLM base URL"
    )
    parser.add_argument(
        "--llm_api_key",
        default="sk-TIQFLyBRDLXqmBvmCbD7674dC8F6426eA7Ed6d2a7a4e75A7",
        help="Main LLM API key",
    )
    parser.add_argument("--llm_model", default="qwen2.5:72b-instruct-nq", help="Main LLM model")
    parser.add_argument(
        "--embedding_base_url", default="http://localhost:8001/v1", help="Embedding base URL"
    )
    parser.add_argument("--embedding_model", default="bge-large-en-v1.5", help="Embedding model")
    parser.add_argument(
        "--judge_base_url", default="https://api2.aigcbest.top/v1", help="Judge LLM base URL"
    )
    parser.add_argument(
        "--judge_api_key",
        default="sk-ddmj3Q8H2EI4r67mEHfrJLtrgGo2YO6SXkSpTMF7YBPTZ96O",
        help="Judge LLM API key",
    )
    parser.add_argument("--judge_model", default="gpt-4o-mini", help="Judge model")
    parser.add_argument(
        "--reuse_db", default=None, help="Reuse a single SQLite db path (for debug)"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    sessions = load_locomo_sessions(
        args.data_path,
        max_sessions=args.max_sessions,
        max_turns=args.max_turns,
        max_qa_per_session=args.max_qa_per_session,
    )
    logger.info("Loaded %d merged sessions from %s", len(sessions), args.data_path)

    llm_client = QwenClient(
        api_key=args.llm_api_key,
        base_url=args.llm_base_url,
        model=args.llm_model,
        embedding_api_key="not-needed",
        embedding_base_url=args.embedding_base_url,
        embedding_model=args.embedding_model,
    )

    judge_client = None
    if args.run_accuracy and not args.skip_judge:
        try:
            judge_client = QwenClient(
                api_key=args.judge_api_key,
                base_url=args.judge_base_url,
                model=args.judge_model,
            )
        except Exception as exc:
            logger.warning(
                "Judge client initialization failed (%s). Accuracy evaluation will run without judge scoring.",
                exc,
            )

    all_qas: list[QAMetrics] = []
    for idx, session in enumerate(sessions):
        logger.info(
            "Evaluating session %s (%d/%d), %d turns, %d QAs",
            session.session_id,
            idx + 1,
            len(sessions),
            session.turn_count,
            len(session.qa_list),
        )
        try:
            qas = run_session(
                session,
                llm_client=llm_client,
                judge_client=judge_client,
                run_accuracy=args.run_accuracy,
                sliding_window_size=args.sliding_window,
                db_path=args.reuse_db,
            )
            all_qas.extend(qas)
            logger.info(
                "Session %s complete: %d QAs evaluated",
                session.session_id,
                len(qas),
            )
        except Exception as exc:
            logger.error(
                "Failed to evaluate session %s: %s",
                session.session_id,
                exc,
                exc_info=args.verbose,
            )

    aggregate = compute_aggregate(all_qas)
    logger.info("=" * 60)
    logger.info("Evaluation complete: %d QAs", aggregate.total_qas)
    logger.info(
        "Avg token saving rate vs baseline: %.2f%%",
        aggregate.avg_token_saving_rate_vs_baseline * 100,
    )
    logger.info(
        "Avg token saving rate vs sliding: %.2f%%",
        aggregate.avg_token_saving_rate_vs_sliding * 100,
    )
    logger.info("Avg baseline latency: %.2f ms", aggregate.avg_baseline_latency_ms)
    logger.info("Avg sliding latency: %.2f ms", aggregate.avg_sliding_latency_ms)
    logger.info("Avg session-mem latency: %.2f ms", aggregate.avg_session_mem_latency_ms)
    logger.info("Median session-mem latency: %.2f ms", aggregate.median_session_mem_latency_ms)
    logger.info("P95 session-mem latency: %.2f ms", aggregate.p95_session_mem_latency_ms)
    if aggregate.avg_judge_score_vs_baseline is not None:
        logger.info("Avg judge score vs baseline: %.3f", aggregate.avg_judge_score_vs_baseline)
    if aggregate.avg_judge_score_vs_sliding is not None:
        logger.info("Avg judge score vs sliding: %.3f", aggregate.avg_judge_score_vs_sliding)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    aggregate.save(args.output)
    logger.info("Results saved to %s", args.output)


if __name__ == "__main__":
    main()
