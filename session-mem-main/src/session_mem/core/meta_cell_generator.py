from __future__ import annotations

from session_mem.core.cell import MemoryCell
from session_mem.llm.base import LLMClient
from session_mem.llm.parser import safe_json_loads
from session_mem.llm.prompts import build_meta_cell_prompt, META_CELL_SCHEMA
from session_mem.utils.tokenizer import TokenEstimator


class MetaCellGenerator:
    """Meta Cell 生成器：维护会话级全局摘要单元。

    首个普通 Cell 生成后，基于该 Cell 创建初始 Meta Cell；
    后续每生成一个普通 Cell，调用 LLM 全量融合重写 Meta Cell。
    """

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.token_estimator = TokenEstimator()

    def generate(
        self,
        session_id: str,
        cells: list[MemoryCell],
        previous_meta: MemoryCell | None = None,
    ) -> MemoryCell:
        """生成或更新会话级 Meta Cell。

        Args:
            session_id: 当前会话 ID。
            cells: 当前会话已生成的全部普通 Cell 列表（按时间序）。
            previous_meta: 上一个版本的 Meta Cell，若为 None 则生成初始版本。

        Returns:
            填充完整的 MemoryCell（cell_type='meta'）。
        """
        if not cells:
            raise ValueError("cells 列表不能为空")

        cell_dicts = [c.to_retrieval_dict() for c in cells]
        for i, c in enumerate(cells):
            cell_dicts[i]["raw_text"] = c.raw_text

        prev_dict = None
        if previous_meta:
            prev_dict = {
                "id": previous_meta.id,
                "summary": previous_meta.summary,
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

        raw_text = data.get("summary", "")
        token_count = self.token_estimator.estimate(raw_text)
        version = (previous_meta.version or 0) + 1 if previous_meta else 1
        meta_id = f"M_{version:03d}"

        summary = data.get("summary", "")
        keywords = data.get("keywords", [])
        entities = data.get("entities", [])
        llm_failed = not data

        # Fallback
        if not summary:
            summary = self._fallback_summary(cells)
        if not keywords:
            keywords = list(dict.fromkeys(kw for c in cells for kw in c.keywords))[:8]
        if not entities:
            entities = list(dict.fromkeys(e for c in cells for e in c.entities))[:5]

        confidence = 0.3 if llm_failed else float(data.get("confidence", 0.5))

        return MemoryCell(
            id=meta_id,
            session_id=session_id,
            cell_type="meta",
            confidence=confidence,
            summary=summary,
            keywords=keywords,
            entities=entities,
            linked_prev=None,
            timestamp_start=cells[0].timestamp_start,
            timestamp_end=cells[-1].timestamp_end,
            vector_id=None,
            token_count=token_count,
            raw_text=raw_text,
            causal_deps=data.get("causal_deps", []),
            status="active",
            version=version,
            linked_cells=[c.id for c in cells],
        )

    def _fallback_summary(self, cells: list[MemoryCell]) -> str:
        summaries = [c.summary for c in cells if c.summary]
        if not summaries:
            return ""
        return " ".join(summaries)[:300]
