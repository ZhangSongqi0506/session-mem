from __future__ import annotations


from session_mem.core.buffer import Turn
from session_mem.core.cell import MemoryCell
from session_mem.llm.base import LLMClient
from session_mem.llm.parser import safe_json_loads
from session_mem.llm.prompts import build_cell_generation_prompt, CELL_GENERATION_SCHEMA
from session_mem.utils.tokenizer import TokenEstimator

import logging

logger = logging.getLogger(__name__)


class CellGenerator:
    """Cell 生成器：将 SenMemBuffer 中的对话打包为 MemoryCell。"""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.token_estimator = TokenEstimator()

    def generate(
        self,
        turns: list[Turn],
        session_id: str,
        cell_id: str,
        linked_prev: str | None = None,
    ) -> MemoryCell:
        raw_text = "\n".join(f"[{t.role}]: {t.content}" for t in turns)
        messages = build_cell_generation_prompt(raw_text)
        try:
            response = self.llm.chat_completion(
                messages,
                temperature=0.3,
                response_format=CELL_GENERATION_SCHEMA,
            )
        except Exception as exc:
            logger.warning("LLM chat_completion failed for cell generation: %s", exc)
            response = ""
        data = safe_json_loads(response) or {}

        token_count = self.token_estimator.estimate(raw_text)
        timestamp_start = turns[0].timestamp if turns else None
        timestamp_end = turns[-1].timestamp if turns else None

        summary = data.get("summary", "")
        keywords = data.get("keywords", [])
        entities = data.get("entities", [])
        llm_failed = not data

        # Fallback：LLM 失败或返回空时，基于原文生成简化摘要
        if not summary:
            summary = self._fallback_summary(raw_text)
        if not keywords:
            keywords = self._fallback_keywords(raw_text)
        if not entities:
            entities = keywords[:5]

        confidence = 0.3 if llm_failed else float(data.get("confidence", 0.5))

        cell = MemoryCell(
            id=cell_id,
            session_id=session_id,
            cell_type=data.get("cell_type", "fact"),
            confidence=confidence,
            summary=summary,
            keywords=keywords,
            entities=entities,
            linked_prev=linked_prev,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,
            vector_id=cell_id,
            token_count=token_count,
            raw_text=raw_text,
            causal_deps=data.get("causal_deps", []),
        )
        return cell

    def _fallback_summary(self, raw_text: str) -> str:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if not lines:
            return ""
        if len(lines) <= 3:
            return " ".join(lines)[:200]
        return " ".join(lines[:3])[:200]

    def _fallback_keywords(self, raw_text: str) -> list[str]:
        import re

        words = re.findall(r"[\u4e00-\u9fa5a-zA-Z0-9]+", raw_text)
        freq: dict[str, int] = {}
        for w in words:
            w = w.lower()
            if len(w) < 2:
                continue
            freq[w] = freq.get(w, 0) + 1
        sorted_words = sorted(freq, key=lambda k: freq[k], reverse=True)
        return sorted_words[:8]
