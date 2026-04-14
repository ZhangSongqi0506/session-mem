from __future__ import annotations

from session_mem.core.cell import MemoryCell
from session_mem.llm.base import LLMClient
from session_mem.llm.parser import safe_json_loads
from session_mem.llm.prompts import build_meta_cell_prompt, META_CELL_SCHEMA
from session_mem.utils.tokenizer import TokenEstimator


class MetaCellGenerator:
    """Meta Cell 生成器：维护会话级全局摘要单元。

    首个普通 Cell 生成后，基于该 Cell 创建初始 Meta Cell；
    后续每生成一个普通 Cell，将"当前 Meta Cell 全文 + 新 Cell 原文"
    送入 LLM 进行增量融合重写。
    """

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.token_estimator = TokenEstimator()

    def generate(
        self,
        session_id: str,
        cell: MemoryCell,
        previous_meta: MemoryCell | None = None,
        linked_cells: list[str] | None = None,
    ) -> MemoryCell:
        """生成或增量更新会话级 Meta Cell。

        Args:
            session_id: 当前会话 ID。
            cell: 用于生成/更新 Meta Cell 的普通 Cell（初始为首个 Cell，更新为最新 Cell）。
            previous_meta: 上一个版本的 Meta Cell，若为 None 则生成初始版本。
            linked_cells: 当前已关联的普通 Cell ID 列表。

        Returns:
            填充完整的 MemoryCell（cell_type='meta'）。
        """
        if not cell:
            raise ValueError("cell 不能为空")

        cell_dict = cell.to_retrieval_dict()
        cell_dict["raw_text"] = cell.raw_text

        prev_dict = None
        if previous_meta:
            prev_dict = {
                "id": previous_meta.id,
                "raw_text": previous_meta.raw_text,
            }

        messages = build_meta_cell_prompt(cell_dict, prev_dict)
        try:
            response = self.llm.chat_completion(
                messages,
                temperature=0.3,
                response_format=META_CELL_SCHEMA,
            )
        except Exception:
            response = ""

        data = safe_json_loads(response) or {}

        llm_summary = data.get("summary", "")
        version = (previous_meta.version or 0) + 1 if previous_meta else 1
        meta_id = f"M_{version:03d}"

        summary = llm_summary
        keywords = data.get("keywords", [])
        entities = data.get("entities", [])
        llm_failed = not data

        # Fallback
        if not summary:
            summary = self._fallback_summary(cell, previous_meta)
        if not keywords:
            keywords = list(dict.fromkeys(cell.keywords))[:8]
        if not entities:
            entities = list(dict.fromkeys(cell.entities))[:5]

        # raw_text 累积保存历史上下文，确保增量更新不丢失信息
        if previous_meta and previous_meta.raw_text:
            raw_text = f"{previous_meta.raw_text}\n\n[Cell {cell.id}]\n{cell.raw_text}"
        else:
            raw_text = f"[Cell {cell.id}]\n{cell.raw_text}"

        token_count = self.token_estimator.estimate(raw_text)
        confidence = 0.3 if llm_failed else float(data.get("confidence", 0.5))

        _linked_cells = list(linked_cells) if linked_cells else []
        if cell.id not in _linked_cells:
            _linked_cells.append(cell.id)

        return MemoryCell(
            id=meta_id,
            session_id=session_id,
            cell_type="meta",
            confidence=confidence,
            summary=summary,
            keywords=keywords,
            entities=entities,
            linked_prev=None,
            timestamp_start=(
                cell.timestamp_start if not previous_meta else previous_meta.timestamp_start
            ),
            timestamp_end=cell.timestamp_end,
            vector_id=None,
            token_count=token_count,
            raw_text=raw_text,
            causal_deps=data.get("causal_deps", []),
            status="active",
            version=version,
            linked_cells=_linked_cells,
        )

    def _fallback_summary(self, cell: MemoryCell, previous_meta: MemoryCell | None) -> str:
        if previous_meta and previous_meta.summary:
            return f"{previous_meta.summary} {cell.summary}"[:300]
        return cell.summary[:300]
