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
from benchmarks.metrics import SessionMetrics, compute_aggregate, judge_answer
from benchmarks.prompt_assembler import PromptAssembler

logger = logging.getLogger(__name__)


def build_memory_system(session_id: str, db_path: str, llm_client: QwenClient) -> MemorySystem:
    """为指定会话构建 MemorySystem 实例。"""
    backend = SQLiteBackend(db_path)
    return MemorySystem(
        session_id=session_id,
        llm_client=llm_client,
        vector_index=backend.vector_index,
        cell_store=backend.cell_store,
        text_store=backend.text_store,
        meta_cell_store=backend,
        embedding_client=llm_client,
    )


def run_session(
    session,
    llm_client: QwenClient,
    judge_client: QwenClient | None,
    run_accuracy: bool,
    db_path: str | None = None,
) -> SessionMetrics:
    """评估单条 session：Token 节省率、延迟、可选准确率。"""
    metrics = SessionMetrics(session_id=session.session_id)
    assembler = PromptAssembler()

    # 1. Baseline tokens
    baseline_msgs, baseline_tokens = assembler.build_baseline(session.turns, query=session.question)
    metrics.baseline_tokens = baseline_tokens

    # 2. session-mem 逐轮写入
    if db_path is None:
        db_fd = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = db_fd.name
        db_fd.close()

    ms = build_memory_system(session.session_id, db_path, llm_client)

    for turn in session.turns:
        ms.add_turn(
            role=turn["role"],
            content=turn["content"],
            timestamp=turn["timestamp"] or "2026-04-14T00:00:00Z",
        )

    # 3. 如果有问题，测量 retrieve_context 延迟和 token 数
    query = session.question or ""
    if query:
        start = time.perf_counter()
        wm = ms.retrieve_context(query, hot_zone_turns=2, top_k=2)
        elapsed_ms = (time.perf_counter() - start) * 1000
        metrics.retrieve_latency_ms = elapsed_ms

        prompt = wm.to_prompt()
        content = prompt[0]["content"] if prompt else ""
        metrics.session_mem_tokens = TokenEstimator().estimate(content)
    else:
        # 没有问题时，用 hot_zone + 全部 Cell 组装 prompt 做 token 对比
        wm = ms.retrieve_context("", hot_zone_turns=2, top_k=2)
        prompt = wm.to_prompt()
        content = prompt[0]["content"] if prompt else ""
        metrics.session_mem_tokens = TokenEstimator().estimate(content)

    if metrics.baseline_tokens > 0:
        metrics.token_saving_rate = (
            metrics.baseline_tokens - metrics.session_mem_tokens
        ) / metrics.baseline_tokens

    # 4. 准确率评估
    if run_accuracy and query and judge_client:
        try:
            baseline_resp = llm_client.chat_completion(messages=baseline_msgs, temperature=0.3)
            metrics.baseline_answer = baseline_resp.strip()
        except Exception as exc:
            logger.warning("Baseline answer failed for %s: %s", session.session_id, exc)
            metrics.baseline_answer = ""

        try:
            session_mem_msgs = wm.to_prompt()
            if session_mem_msgs:
                session_mem_msgs.append({"role": "user", "content": query})
            else:
                session_mem_msgs = [{"role": "user", "content": query}]
            sm_resp = llm_client.chat_completion(messages=session_mem_msgs, temperature=0.3)
            metrics.session_mem_answer = sm_resp.strip()
        except Exception as exc:
            logger.warning("Session-mem answer failed for %s: %s", session.session_id, exc)
            metrics.session_mem_answer = ""

        if session.answer and metrics.baseline_answer and metrics.session_mem_answer:
            try:
                metrics.judge_score = judge_answer(
                    question=query,
                    ground_truth=session.answer,
                    baseline_answer=metrics.baseline_answer,
                    session_mem_answer=metrics.session_mem_answer,
                    judge_client=judge_client,
                )
            except Exception as exc:
                logger.warning("Judge failed for %s: %s", session.session_id, exc)

    ms.vector_index.conn.close()
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="LoCoMo benchmark for session-mem")
    parser.add_argument("--data_path", required=True, help="Path to LoCoMo JSON/JSONL file")
    parser.add_argument("--max_sessions", type=int, default=None, help="Max sessions to evaluate")
    parser.add_argument("--max_turns", type=int, default=None, help="Max turns per session")
    parser.add_argument(
        "--output", default="benchmarks/results/locomo_results.json", help="Output JSON path"
    )
    parser.add_argument(
        "--run_accuracy", action="store_true", help="Run LLM answer + judge evaluation"
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
    )
    logger.info("Loaded %d sessions from %s", len(sessions), args.data_path)

    llm_client = QwenClient(
        api_key=args.llm_api_key,
        base_url=args.llm_base_url,
        model=args.llm_model,
        embedding_api_key="not-needed",
        embedding_base_url=args.embedding_base_url,
        embedding_model=args.embedding_model,
    )

    judge_client = None
    if args.run_accuracy:
        judge_client = QwenClient(
            api_key=args.judge_api_key,
            base_url=args.judge_base_url,
            model=args.judge_model,
        )

    results: list[SessionMetrics] = []
    for idx, session in enumerate(sessions):
        logger.info("Evaluating session %s (%d/%d)", session.session_id, idx + 1, len(sessions))
        try:
            sm = run_session(
                session,
                llm_client=llm_client,
                judge_client=judge_client,
                run_accuracy=args.run_accuracy,
                db_path=args.reuse_db,
            )
            results.append(sm)
            logger.info(
                "Session %s: baseline=%d tokens, session-mem=%d tokens, saving=%.2f%%, latency=%.2f ms",
                session.session_id,
                sm.baseline_tokens,
                sm.session_mem_tokens,
                sm.token_saving_rate * 100,
                sm.retrieve_latency_ms,
            )
        except Exception as exc:
            logger.error(
                "Failed to evaluate session %s: %s", session.session_id, exc, exc_info=args.verbose
            )

    aggregate = compute_aggregate(results)
    logger.info("=" * 60)
    logger.info("Evaluation complete: %d sessions", aggregate.total_sessions)
    logger.info("Avg token saving rate: %.2f%%", aggregate.avg_token_saving_rate * 100)
    logger.info("Avg retrieve latency: %.2f ms", aggregate.avg_retrieve_latency_ms)
    logger.info("Median retrieve latency: %.2f ms", aggregate.median_retrieve_latency_ms)
    logger.info("P95 retrieve latency: %.2f ms", aggregate.p95_retrieve_latency_ms)
    if aggregate.avg_judge_score is not None:
        logger.info("Avg judge score: %.3f", aggregate.avg_judge_score)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    aggregate.save(args.output)
    logger.info("Results saved to %s", args.output)


if __name__ == "__main__":
    main()
