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
        cells: list[MemoryCell],
        previous_meta: MemoryCell | None = None,
        linked_cells: list[str] | None = None,
    ) -> MemoryCell:
        """生成或增量更新会话级 Meta Cell。

        Args:
            session_id: 当前会话 ID。
            cells: 本次新产生的全部普通 Cell（1 个或多个）。
            previous_meta: 上一个版本的 Meta Cell，若为 None 则生成初始版本。
            linked_cells: 当前已关联的普通 Cell ID 列表。

        Returns:
            填充完整的 MemoryCell（cell_type='meta'）。
        """
        if not cells:
            raise ValueError("cells 不能为空")

        cell_dicts = []
        for cell in cells:
            d = cell.to_retrieval_dict()
            d["raw_text"] = cell.raw_text
            cell_dicts.append(d)

        prev_dict = None
        if previous_meta:
            prev_dict = {
                "id": previous_meta.id,
                "raw_text": previous_meta.raw_text,
            }

        messages = build_meta_cell_prompt(cell_dicts, prev_dict)
        try:
            response = self.llm.chat_completion(
                messages,
                temperature=0.3,
                response_format=META_CELL_SCHEMA,
            )
        except Exception:
            response = ""

        data = safe_json_loads(response) or {}
        if not isinstance(data, dict):
            data = {}

        llm_summary = data.get("summary", "")
        version = (previous_meta.version or 0) + 1 if previous_meta else 1
        meta_id = f"M_{version:03d}"

        summary = llm_summary
        keywords = data.get("keywords", [])
        entities = data.get("entities", [])
        llm_failed = not data

        # Fallback
        if not summary:
            summary = self._fallback_summary(cells, previous_meta)
        if not keywords:
            all_keywords = []
            for c in cells:
                all_keywords.extend(c.keywords or [])
            keywords = list(dict.fromkeys(all_keywords))[:8]
        if not entities:
            all_entities = []
            for c in cells:
                all_entities.extend(c.entities or [])
            entities = list(dict.fromkeys(all_entities))[:5]

        # Meta Cell 的 raw_text 必须存储 LLM 返回的 summary（全局摘要），不存任何原文拼接
        if llm_summary:
            raw_text = llm_summary
        else:
            raw_text = self._fallback_summary(cells, previous_meta)

        token_count = self.token_estimator.estimate(raw_text)
        confidence = 0.3 if llm_failed else float(data.get("confidence", 0.5))

        _linked_cells = list(linked_cells) if linked_cells else []
        for c in cells:
            if c.id not in _linked_cells:
                _linked_cells.append(c.id)

        first_cell = cells[0]
        last_cell = cells[-1]

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
                first_cell.timestamp_start if not previous_meta else previous_meta.timestamp_start
            ),
            timestamp_end=last_cell.timestamp_end,
            vector_id=None,
            token_count=token_count,
            raw_text=raw_text,
            causal_deps=data.get("causal_deps", []),
            status="active",
            version=version,
            linked_cells=_linked_cells,
        )

    def _fallback_summary(self, cells: list[MemoryCell], previous_meta: MemoryCell | None) -> str:
        cell_summaries = " ".join([c.summary for c in cells if c.summary])
        if previous_meta and previous_meta.summary:
            return f"{previous_meta.summary} {cell_summaries}"
        return cell_summaries
