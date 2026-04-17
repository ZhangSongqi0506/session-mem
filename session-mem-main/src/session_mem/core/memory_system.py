from __future__ import annotations

import logging
from typing import Any

import re

from session_mem.config import RetrievalConfig
from session_mem.core.buffer import SenMemBuffer, ShortMemBuffer, Turn
from session_mem.core.cell import MemoryCell
from session_mem.core.cell_generator import CellGenerator
from session_mem.core.boundary_detector import SemanticBoundaryDetector
from session_mem.core.meta_cell_generator import MetaCellGenerator
from session_mem.core.working_memory import WorkingMemory
from session_mem.llm.base import LLMClient
from session_mem.retrieval.hybrid_search import HybridSearcher
from session_mem.retrieval.query_rewriter import QueryRewriter
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
        query_rewriter: QueryRewriter | None = None,
        meta_cell_store: Any = None,
        embedding_client: LLMClient | None = None,
    ):
        self.session_id = session_id
        self.llm = llm_client
        self.vector_index = vector_index
        self.cell_store = cell_store
        self.text_store = text_store
        self.meta_cell_store = meta_cell_store
        self.embedding_client = embedding_client

        self.hybrid = hybrid_searcher or HybridSearcher(
            vector_index=vector_index,
            cell_store=cell_store,
            session_id=session_id,
            embedding_client=embedding_client,
        )
        token_estimator = TokenEstimator().estimate
        self.query_rewriter = query_rewriter or QueryRewriter(
            llm_client=llm_client,
            token_estimator=token_estimator,
        )

        self.sen_buffer = SenMemBuffer(session_id=session_id)
        self.sen_buffer.set_token_estimator(token_estimator)
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
            cell = self._generate_cell(cell_turns, fragmented=False)
            if cell and self.meta_cell_store is not None:
                self._update_meta_cell([cell])
            return

        # 2. 硬上限强制切分
        if self.sen_buffer.is_hard_limit_reached():
            cell_turns = self.sen_buffer.extract_for_cell(len(self.sen_buffer.turns))
            cell = self._generate_cell(cell_turns, fragmented=True)
            if cell and self.meta_cell_store is not None:
                self._update_meta_cell([cell])
            return

        # 3. 软限触发语义边界检测
        if self.sen_buffer.should_trigger_check():
            split_indices = self.boundary_detector.should_split(self.sen_buffer.turns)
            if split_indices:
                segments = self.sen_buffer.extract_segments(split_indices)
                new_cells: list[MemoryCell] = []
                for segment in segments:
                    cell = self._generate_cell(segment, fragmented=False)
                    if cell:
                        new_cells.append(cell)
                if new_cells and self.meta_cell_store is not None:
                    self._update_meta_cell(new_cells)
            return

    def retrieve_context(
        self,
        query: str,
        hot_zone_turns: int = 2,
        top_k: int = 2,
        extra_turns: list[dict[str, str]] | None = None,
    ) -> WorkingMemory:
        """
        检索相关 Cell 并组装 Working Memory。
        Meta Cell 无条件前置。
        """
        # 1. 构建热区
        hot_zone = self._build_hot_zone(hot_zone_turns, extra_turns=extra_turns)

        # 2. 查询重写
        rewritten_query = self.query_rewriter.rewrite(query, hot_zone)

        # 3. 双路召回（带分数的全部候选）
        candidate_scores: list[tuple[str, float]] = []
        if self.hybrid:
            candidate_scores = self.hybrid.search_with_scores(rewritten_query)

        # 4. 阈值筛选 + 动态上下限
        threshold = RetrievalConfig.MEMORY_SYSTEM_THRESHOLD
        selected_ids = [cid for cid, score in candidate_scores if score >= threshold]

        total_cells = len(self.cell_store.list_by_session(self.session_id))
        min_cells = max(2, min(5, total_cells // 10))
        max_cells = max(min_cells + 1, min(8, total_cells // 3))

        if len(selected_ids) < min_cells:
            selected_ids = [cid for cid, _ in candidate_scores[:min_cells]]
        if len(selected_ids) > max_cells:
            selected_ids = selected_ids[:max_cells]

        # 5. 加载 selected Cells
        activated: list[MemoryCell] = []
        seen: set[str] = set()
        for cid in selected_ids:
            cell = self.cell_store.get(cid)
            if cell and cell.id not in seen:
                cell.raw_text = self.text_store.load(cid)
                activated.append(cell)
                seen.add(cell.id)

        # 6. 实体扩展激活（BM25 替代硬匹配）
        extra_limit = 3
        all_entities: set[str] = set()
        for cell in activated:
            for entity in cell.entities or []:
                all_entities.add(entity)

        if all_entities and self.hybrid:
            expansion_query = " ".join(all_entities)
            # 9.2.1: 不对候选池做前置过滤，全部 cell 参与 BM25 评分，再从中取未入选的 top extra_limit
            session_cells = self.cell_store.list_by_session(self.session_id)
            for c in session_cells:
                c.raw_text = self.text_store.load(c.id) or ""
            if session_cells:
                bm25_scores = self.hybrid.keyword_scores(expansion_query, session_cells)
                # 按 BM25 得分全局排序，依次跳过已在 seen 中的 cell，取满 extra_limit 为止
                sorted_candidates = sorted(bm25_scores.items(), key=lambda x: x[1], reverse=True)
                added = 0
                for cid, score in sorted_candidates:
                    if score <= 0 or added >= extra_limit:
                        break
                    if cid in seen:
                        continue
                    cell = self.cell_store.get(cid)
                    if cell:
                        cell.raw_text = self.text_store.load(cid)
                        activated.append(cell)
                        seen.add(cell.id)
                        added += 1

        # 8. 按 timestamp_start 升序排列 activated cells，恢复自然叙事顺序
        activated.sort(key=lambda c: (c.timestamp_start is None, c.timestamp_start or ""))

        # 9. 获取 active Meta Cell
        meta_cell: MemoryCell | None = None
        if self.meta_cell_store is not None:
            meta_cell = getattr(self.meta_cell_store, "get_active_meta_cell", lambda sid: None)(
                self.session_id
            )

        # 10. 组装
        wm = WorkingMemory(
            hot_zone=hot_zone,
            activated_cells=activated,
            query=rewritten_query,
            meta_cell=meta_cell,
        )
        return wm

    def _update_meta_cell(self, newest_cells: list[MemoryCell]) -> None:
        """触发 Meta Cell 的生成或增量融合更新。"""
        if self.meta_cell_store is None or not newest_cells:
            return
        previous_meta = getattr(self.meta_cell_store, "get_active_meta_cell", lambda sid: None)(
            self.session_id
        )
        linked_cells = list(previous_meta.linked_cells) if previous_meta else []
        meta_cell = self.meta_cell_generator.generate(
            self.session_id,
            newest_cells,
            previous_meta=previous_meta,
            linked_cells=linked_cells,
        )
        getattr(self.meta_cell_store, "save_meta_cell", lambda c: None)(meta_cell)

    def _build_hot_zone(
        self,
        n_turns: int,
        extra_turns: list[dict[str, str]] | None = None,
    ) -> list[str]:
        # SenMemBuffer 是零压缩缓冲区，全部内容都属于热区
        hot_zone = [f"[{t.role}]: {t.content}" for t in self.sen_buffer.turns]
        if extra_turns:
            for et in extra_turns:
                hot_zone.append(f"[{et.get('role', 'user')}]: {et.get('content', '')}")
        return hot_zone

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

    def _generate_cell(self, turns: list[Turn], fragmented: bool = False) -> MemoryCell | None:
        """将提取的轮次生成 Cell 并持久化。"""
        if not turns:
            return None
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
        return cell
