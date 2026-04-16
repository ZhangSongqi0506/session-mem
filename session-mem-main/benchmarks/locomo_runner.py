"""LoCoMo benchmark runner for session-mem."""

from __future__ import annotations

import argparse
import concurrent.futures
import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from session_mem.core.memory_system import MemorySystem
from session_mem.llm.qwen_client import QwenClient
from session_mem.storage.sqlite_backend import SQLiteBackend
from session_mem.utils.tokenizer import TokenEstimator

from benchmarks.data_loader import load_locomo_sessions
from benchmarks.metrics import QAMetrics, compute_aggregate, judge_answer
from benchmarks.prompt_assembler import PromptAssembler

logger = logging.getLogger(__name__)


ANSWER_INSTRUCTION = (
    "Based only on the provided context, answer directly and concisely. "
    "Quote the relevant sentence explicitly. "
    "Do not infer or over-interpret. "
    "If the question asks about time, dates, or when something happened, "
    "you must answer with the specific absolute timestamp or date explicitly."
)


def _answer(llm_client, messages: list[dict[str, str]]) -> str:
    """调用 LLM 生成回答。"""
    if not messages:
        return ""
    instructed = [{"role": "system", "content": ANSWER_INSTRUCTION}] + messages
    try:
        resp = llm_client.chat_completion(messages=instructed, temperature=0.3)
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

        # --- 滑窗基线 ---
        slide_msgs, slide_tokens = assembler.build_sliding_window(
            session.turns, query=question, window_size=sliding_window_size
        )
        metrics.sliding_tokens = slide_tokens

        # --- session-mem ---
        start = time.perf_counter()
        extra_turns = [
            {
                "role": "user",
                "content": question,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]
        wm = ms.retrieve_context(question, hot_zone_turns=2, top_k=2, extra_turns=extra_turns)
        metrics.session_mem_latency_ms = (time.perf_counter() - start) * 1000

        sm_prompt = wm.to_prompt()
        sm_msgs = list(sm_prompt)
        if sm_msgs:
            sm_msgs.append({"role": "user", "content": question})
        else:
            sm_msgs = [{"role": "user", "content": question}]

        content = sm_msgs[0]["content"] if sm_msgs else ""
        estimator = TokenEstimator()
        metrics.session_mem_tokens = estimator.estimate(content)

        # 拆解 session-mem Token 构成
        if wm.meta_cell and wm.meta_cell.raw_text:
            metrics.session_mem_meta_cell_tokens = estimator.estimate(wm.meta_cell.raw_text)
        if wm.hot_zone:
            metrics.session_mem_hot_zone_tokens = estimator.estimate("\n\n".join(wm.hot_zone))
        metrics.session_mem_activated_cell_count = len(wm.activated_cells)
        metrics.session_mem_activated_cells = [
            {
                "cell_id": cell.id,
                "cell_type": cell.cell_type,
                "summary": cell.summary or "",
                "token_count": estimator.estimate(cell.raw_text or ""),
            }
            for cell in wm.activated_cells
        ]

        # session-mem 内部 token 开销估算（QueryRewriter + Embedding）
        hot_zone_text = "\n\n".join(wm.hot_zone)
        internal_tokens = 0
        # Embedding tokens
        internal_tokens += estimator.estimate(wm.query)
        # QueryRewriter prompt tokens（若触发条件满足）
        tokens = estimator.estimate(question)
        pronouns = (
            "这",
            "那",
            "刚才",
            "之前",
            "它",
            "他",
            "她",
            "这个",
            "那个",
            "this",
            "that",
            "it",
            "he",
            "she",
            "they",
            "them",
            "these",
            "those",
        )
        if tokens < 10 or any(w in question.lower() for w in pronouns):
            rewrite_system = (
                "你是一个查询重写助手。请根据最近对话上下文，"
                "将用户的短查询或含指代词的查询扩展为明确、完整的句子。"
                "只输出扩展后的查询，不要解释。"
            )
            rewrite_prompt = (
                f"最近对话：\n{hot_zone_text}\n\n" f"用户查询：{question}\n\n扩展后查询："
            )
            internal_tokens += estimator.estimate(rewrite_system + rewrite_prompt)
        metrics.session_mem_internal_tokens = internal_tokens

        # Token 节省率
        if metrics.baseline_tokens > 0:
            metrics.token_saving_rate_vs_baseline = (
                metrics.baseline_tokens - metrics.session_mem_tokens
            ) / metrics.baseline_tokens
        if metrics.sliding_tokens > 0:
            metrics.token_saving_rate_vs_sliding = (
                metrics.sliding_tokens - metrics.session_mem_tokens
            ) / metrics.sliding_tokens

        # --- 方法级并发：三种回答生成 ---
        if run_accuracy:

            def _timed_answer(msgs: list[dict[str, str]]) -> tuple[str, float]:
                start = time.perf_counter()
                ans = _answer(llm_client, msgs)
                latency = (time.perf_counter() - start) * 1000
                return ans, latency

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                future_baseline = executor.submit(_timed_answer, baseline_msgs)
                future_sliding = executor.submit(_timed_answer, slide_msgs)
                future_sm = executor.submit(_timed_answer, sm_msgs)

                metrics.baseline_answer, metrics.baseline_latency_ms = future_baseline.result()
                metrics.sliding_answer, metrics.sliding_latency_ms = future_sliding.result()
                metrics.session_mem_answer, metrics.session_mem_latency_ms_extra = (
                    future_sm.result()
                )
                # session_mem_latency_ms 仅包含 retrieve_context 时间；
                # 若希望总 latency 包含生成时间，可累加：
                # metrics.session_mem_latency_ms += metrics.session_mem_latency_ms_extra
        else:
            metrics.baseline_latency_ms = 0.0
            metrics.sliding_latency_ms = 0.0

        # Judge 评分（串行）
        if run_accuracy and judge_client:
            # 三个回答各自 vs ground_truth
            if metrics.baseline_answer:
                try:
                    metrics.baseline_judge_score = judge_answer(
                        question=question,
                        ground_truth=ground_truth,
                        candidate_answer=metrics.baseline_answer,
                        judge_client=judge_client,
                    )
                except Exception as exc:
                    logger.warning("Judge baseline vs ground_truth failed: %s", exc)

            if metrics.sliding_answer:
                try:
                    metrics.sliding_judge_score = judge_answer(
                        question=question,
                        ground_truth=ground_truth,
                        candidate_answer=metrics.sliding_answer,
                        judge_client=judge_client,
                    )
                except Exception as exc:
                    logger.warning("Judge sliding vs ground_truth failed: %s", exc)

            if metrics.session_mem_answer:
                try:
                    metrics.session_mem_judge_score = judge_answer(
                        question=question,
                        ground_truth=ground_truth,
                        candidate_answer=metrics.session_mem_answer,
                        judge_client=judge_client,
                    )
                except Exception as exc:
                    logger.warning("Judge session-mem vs ground_truth failed: %s", exc)

                # session-mem vs baseline / sliding（交叉对比）
                if metrics.baseline_answer:
                    try:
                        metrics.judge_score_vs_baseline = judge_answer(
                            question=question,
                            ground_truth=metrics.baseline_answer,
                            candidate_answer=metrics.session_mem_answer,
                            judge_client=judge_client,
                        )
                    except Exception as exc:
                        logger.warning("Judge vs baseline failed: %s", exc)

                if metrics.sliding_answer:
                    try:
                        metrics.judge_score_vs_sliding = judge_answer(
                            question=question,
                            ground_truth=metrics.sliding_answer,
                            candidate_answer=metrics.session_mem_answer,
                            judge_client=judge_client,
                        )
                    except Exception as exc:
                        logger.warning("Judge vs sliding failed: %s", exc)

        logger.info(
            "QA %s-%d | sm_tokens=%d (meta=%d hot_zone=%d cells=%d internal=%d) | save_base=%.2f%% | save_slide=%.2f%%",
            metrics.session_id,
            metrics.question_id,
            metrics.session_mem_tokens,
            metrics.session_mem_meta_cell_tokens,
            metrics.session_mem_hot_zone_tokens,
            metrics.session_mem_activated_cell_count,
            metrics.session_mem_internal_tokens,
            metrics.token_saving_rate_vs_baseline * 100,
            metrics.token_saving_rate_vs_sliding * 100,
        )

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
    parser.add_argument(
        "--max_workers",
        type=int,
        default=1,
        help="Max concurrent sessions to evaluate (default: 1). Ignored if --reuse_db is set.",
    )
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

    # 并发冲突保护：reuse_db 不支持多线程共享
    max_workers = args.max_workers
    if args.reuse_db and max_workers > 1:
        logger.warning(
            "--reuse_db is not compatible with --max_workers > 1. Falling back to max_workers=1."
        )
        max_workers = 1

    def _evaluate_one(session):
        logger.info(
            "Evaluating session %s, %d turns, %d QAs",
            session.session_id,
            session.turn_count,
            len(session.qa_list),
        )
        qas = run_session(
            session,
            llm_client=llm_client,
            judge_client=judge_client,
            run_accuracy=args.run_accuracy,
            sliding_window_size=args.sliding_window,
            db_path=args.reuse_db,
        )
        logger.info("Session %s complete: %d QAs evaluated", session.session_id, len(qas))
        return qas

    all_qas: list[QAMetrics] = []
    completed = 0
    if max_workers > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_session = {
                executor.submit(_evaluate_one, session): session for session in sessions
            }
            for future in concurrent.futures.as_completed(future_to_session):
                session = future_to_session[future]
                try:
                    qas = future.result()
                    all_qas.extend(qas)
                except Exception as exc:
                    logger.error(
                        "Failed to evaluate session %s: %s",
                        session.session_id,
                        exc,
                        exc_info=args.verbose,
                    )
                completed += 1
                logger.info("Progress: %d/%d sessions completed", completed, len(sessions))
    else:
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
                qas = _evaluate_one(session)
                all_qas.extend(qas)
            except Exception as exc:
                logger.error(
                    "Failed to evaluate session %s: %s",
                    session.session_id,
                    exc,
                    exc_info=args.verbose,
                )
            completed += 1
            logger.info("Progress: %d/%d sessions completed", completed, len(sessions))

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
    if aggregate.avg_baseline_judge_score is not None:
        logger.info("Avg baseline judge: %.3f", aggregate.avg_baseline_judge_score)
    if aggregate.avg_sliding_judge_score is not None:
        logger.info("Avg sliding judge: %.3f", aggregate.avg_sliding_judge_score)
    if aggregate.avg_session_mem_judge_score is not None:
        logger.info("Avg session-mem judge: %.3f", aggregate.avg_session_mem_judge_score)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    aggregate.save(args.output)
    logger.info("Results saved to %s", args.output)

    report_path = Path(args.output).with_suffix(".txt")
    aggregate.save_text_report(str(report_path))
    logger.info("Text report saved to %s", report_path)


if __name__ == "__main__":
    main()
