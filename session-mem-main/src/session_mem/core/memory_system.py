from __future__ import annotations

import logging
from typing import Any

import re

from session_mem.core.buffer import SenMemBuffer, ShortMemBuffer, Turn
from session_mem.core.cell import MemoryCell
from session_mem.core.cell_generator import CellGenerator
from session_mem.core.boundary_detector import SemanticBoundaryDetector
from session_mem.core.meta_cell_generator import MetaCellGenerator
from session_mem.core.working_memory import WorkingMemory
from session_mem.llm.base import LLMClient
from session_mem.retrieval.hybrid_search import HybridSearcher
from session_mem.storage.base import CellStore, TextStore, VectorIndex
from session_mem.utils.tokenizer import TokenEstimator

logger = logging.getLogger(__name__)


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
        meta_cell_store: Any = None,
        embedding_client: LLMClient | None = None,
    ):
        self.session_id = session_id
        self.llm = llm_client
        self.vector_index = vector_index
        self.cell_store = cell_store
        self.text_store = text_store
        self.hybrid = hybrid_searcher
        self.meta_cell_store = meta_cell_store
        self.embedding_client = embedding_client

        self.sen_buffer = SenMemBuffer(session_id=session_id)
        self.sen_buffer.set_token_estimator(TokenEstimator().estimate)
        self.short_buffer = ShortMemBuffer(session_id=session_id, cell_store=cell_store)
        self.cell_generator = CellGenerator(self.llm)
        self.boundary_detector = SemanticBoundaryDetector(self.llm)
        self.meta_cell_generator = MetaCellGenerator(self.llm)
        self._cell_counter = self._resolve_max_cell_id(cell_store)
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
            split_indices = self.boundary_detector.should_split(self.sen_buffer.turns)
            if split_indices:
                segments = self.sen_buffer.extract_segments(split_indices)
                for i, segment in enumerate(segments):
                    self._generate_cell(
                        segment,
                        fragmented=False,
                        trigger_meta=(i == len(segments) - 1),
                    )
            return

    def retrieve_context(
        self,
        query: str,
        hot_zone_turns: int = 2,
        top_k: int = 2,
    ) -> WorkingMemory:
        """
        检索相关 Cell 并组装 Working Memory。
        Meta Cell 无条件前置。
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

        # 4. 获取 active Meta Cell
        meta_cell: MemoryCell | None = None
        if self.meta_cell_store is not None:
            meta_cell = getattr(self.meta_cell_store, "get_active_meta_cell", lambda sid: None)(
                self.session_id
            )

        # 5. 组装
        wm = WorkingMemory(
            hot_zone=hot_zone,
            activated_cells=activated,
            query=query,
            meta_cell=meta_cell,
        )
        return wm

    def _update_meta_cell(self, newest_cell: MemoryCell) -> None:
        """触发 Meta Cell 的生成或增量融合更新。"""
        if self.meta_cell_store is None:
            return
        previous_meta = getattr(self.meta_cell_store, "get_active_meta_cell", lambda sid: None)(
            self.session_id
        )
        linked_cells = list(previous_meta.linked_cells) if previous_meta else []
        meta_cell = self.meta_cell_generator.generate(
            self.session_id,
            newest_cell,
            previous_meta=previous_meta,
            linked_cells=linked_cells,
        )
        getattr(self.meta_cell_store, "save_meta_cell", lambda c: None)(meta_cell)

    def _build_hot_zone(self, n_turns: int) -> list[str]:
        recent = self.sen_buffer.turns[-n_turns:]
        return [f"[{t.role}]: {t.content}" for t in recent]

    def _resolve_max_cell_id(self, cell_store: CellStore) -> int:
        """从持久化存储中解析当前会话的最大 Cell 序号，避免重启后 ID 冲突。"""
        try:
            existing = cell_store.list_by_session(self.session_id)
            nums = []
            for c in existing:
                match = re.match(r"^C_(\d+)$", c.id)
                if match:
                    nums.append(int(match.group(1)))
            return max(nums) if nums else 0
        except Exception as exc:
            logger.warning("Failed to resolve max cell id: %s", exc)
            return 0

    def _next_cell_id(self) -> str:
        self._cell_counter += 1
        return f"C_{self._cell_counter:03d}"

    def _generate_cell(
        self, turns: list[Turn], fragmented: bool = False, trigger_meta: bool = True
    ) -> None:
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

        # Embedding 向量写入
        if self.embedding_client is not None:
            try:
                embeddings = self.embedding_client.embed([cell.raw_text])
                if embeddings:
                    self.vector_index.add(cell.vector_id or cell.id, embeddings[0])
            except Exception as exc:
                logger.warning("Embedding failed for cell %s: %s", cell.id, exc)

        self.short_buffer.add(cell)
        self._last_cell_id = cell.id

        # Meta Cell 生成 / 更新（批量生成时仅触发最后一次）
        if trigger_meta and self.meta_cell_store is not None:
            self._update_meta_cell(cell)
