from __future__ import annotations


from session_mem.core.buffer import SenMemBuffer, ShortMemBuffer, Turn
from session_mem.core.cell import MemoryCell
from session_mem.core.working_memory import WorkingMemory
from session_mem.llm.base import LLMClient
from session_mem.retrieval.hybrid_search import HybridSearcher
from session_mem.storage.base import CellStore, TextStore, VectorIndex


class MemorySystem:
    """
    session-mem 主入口。
    对外暴露 add_turn() 和 retrieve_context()。
    """

    def __init__(
        self,
        session_id: str,
        llm_client: LLMClient,
        vector_index: VectorIndex,
        cell_store: CellStore,
        text_store: TextStore,
        hybrid_searcher: HybridSearcher | None = None,
    ):
        self.session_id = session_id
        self.llm = llm_client
        self.vector_index = vector_index
        self.cell_store = cell_store
        self.text_store = text_store
        self.hybrid = hybrid_searcher

        self.sen_buffer = SenMemBuffer(session_id=session_id)
        self.short_buffer = ShortMemBuffer()
        self._cell_counter = 0

    def add_turn(self, role: str, content: str, timestamp: str) -> None:
        """写入新对话轮次。"""
        turn = Turn(role=role, content=content, timestamp=timestamp)
        self.sen_buffer.add_turn(turn)
        # TODO: 触发语义边界检测、Cell 生成

    def retrieve_context(
        self,
        query: str,
        hot_zone_turns: int = 2,
        top_k: int = 2,
    ) -> WorkingMemory:
        """
        检索相关 Cell 并组装 Working Memory。
        """
        # 1. 构建热区
        hot_zone = self._build_hot_zone(hot_zone_turns)

        # 2. 双路召回
        candidate_ids: list[str] = []
        if self.hybrid:
            candidate_ids = self.hybrid.search(query, top_k=top_k)

        # 3. 全量回溯原文
        activated: list[MemoryCell] = []
        for cid in candidate_ids[:top_k]:
            cell = self.cell_store.get(cid)
            if cell:
                cell.raw_text = self.text_store.load(cid)
                activated.append(cell)

        # 4. 组装
        wm = WorkingMemory(
            hot_zone=hot_zone,
            activated_cells=activated,
            query=query,
        )
        return wm

    def _build_hot_zone(self, n_turns: int) -> list[str]:
        recent = self.sen_buffer.turns[-n_turns:]
        return [f"[{t.role}]: {t.content}" for t in recent]

    def _next_cell_id(self) -> str:
        self._cell_counter += 1
        return f"C_{self._cell_counter:03d}"
