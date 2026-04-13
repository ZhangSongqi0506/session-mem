from __future__ import annotations

from datetime import datetime, timezone

from session_mem.core.buffer import Turn
from session_mem.core.cell import MemoryCell
from session_mem.llm.base import LLMClient
from session_mem.llm.parser import safe_json_loads
from session_mem.llm.prompts import build_cell_generation_prompt, CELL_GENERATION_SCHEMA
from session_mem.utils.tokenizer import TokenEstimator


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
        response = self.llm.chat_completion(
            messages,
            temperature=0.3,
            response_format=CELL_GENERATION_SCHEMA,
        )
        data = safe_json_loads(response) or {}

        token_count = self.token_estimator.estimate(raw_text)
        timestamp_start = turns[0].timestamp if turns else None
        timestamp_end = turns[-1].timestamp if turns else None

        cell = MemoryCell(
            id=cell_id,
            session_id=session_id,
            cell_type=data.get("cell_type", "fact"),
            confidence=float(data.get("confidence", 0.5)),
            summary=data.get("summary", ""),
            keywords=data.get("keywords", []),
            entities=data.get("entities", []),
            linked_prev=linked_prev,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,
            vector_id=cell_id,
            token_count=token_count,
            raw_text=raw_text,
            causal_deps=data.get("causal_deps", []),
        )
        return cell
