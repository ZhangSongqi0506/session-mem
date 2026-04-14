from __future__ import annotations


from session_mem.core.buffer import SenMemBuffer, ShortMemBuffer, Turn
from session_mem.core.cell import MemoryCell
from session_mem.core.cell_generator import CellGenerator
from session_mem.core.boundary_detector import SemanticBoundaryDetector
from session_mem.core.working_memory import WorkingMemory
from session_mem.llm.base import LLMClient
from session_mem.retrieval.hybrid_search import HybridSearcher
from session_mem.storage.base import CellStore, TextStore, VectorIndex
from session_mem.utils.tokenizer import TokenEstimator


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
        self.sen_buffer.set_token_estimator(TokenEstimator().estimate)
        self.short_buffer = ShortMemBuffer()
        self.cell_generator = CellGenerator(self.llm)
        self.boundary_detector = SemanticBoundaryDetector(self.llm)
        self._cell_counter = 0
        self._last_cell_id: str | None = None

    def add_turn(self, role: str, content: str, timestamp: str) -> None:
        """写入新对话轮次，内部触发切分检测与 Cell 生成。"""
        turn = Turn(role=role, content=content, timestamp=timestamp)
        self.sen_buffer.add_turn(turn)

        # 1. 时间间隔强制切分
        if self.sen_buffer.gap_detected():
            cell_turns = self.sen_buffer.extract_for_cell(len(self.sen_buffer.turns))
            self._generate_cell(cell_turns, fragmented=False)
            return

        # 2. 硬上限强制切分
        if self.sen_buffer.is_hard_limit_reached():
            cell_turns = self.sen_buffer.extract_for_cell(len(self.sen_buffer.turns))
            self._generate_cell(cell_turns, fragmented=True)
            return

        # 3. 软限触发语义边界检测
        if self.sen_buffer.should_trigger_check():
            if self.boundary_detector.should_split(self.sen_buffer.turns):
                cutoff = max(1, len(self.sen_buffer.turns) - 1)
                cell_turns = self.sen_buffer.extract_for_cell(cutoff)
                self._generate_cell(cell_turns, fragmented=False)

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

    def _generate_cell(self, turns: list[Turn], fragmented: bool = False) -> None:
        """将提取的轮次生成 Cell 并持久化。"""
        if not turns:
            return
        cell_id = self._next_cell_id()
        cell = self.cell_generator.generate(
            turns,
            self.session_id,
            cell_id,
            linked_prev=self._last_cell_id,
        )
        if fragmented:
            cell.cell_type = "fragmented"
        self.cell_store.save(cell)
        self.text_store.save(cell.id, cell.raw_text, cell.token_count)
        # Embedding 向量写入在 Phase 4 实现
        self.short_buffer.add(cell)
        self._last_cell_id = cell.id
