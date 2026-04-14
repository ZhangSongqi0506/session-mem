from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Callable

from session_mem.llm.base import LLMClient
from session_mem.storage.base import CellStore, VectorIndex

logger = logging.getLogger(__name__)


class HybridSearcher:
    """双路召回：向量相似度 + 关键词桥接，支持低置信度 fallback。"""

    def __init__(
        self,
        vector_index: VectorIndex,
        cell_store: CellStore,
        session_id: str,
        vector_weight: float = 0.75,
        keyword_weight: float = 0.25,
        embedding_client: LLMClient | None = None,
        embed_fn: Callable[[str], list[float] | None] | None = None,
    ):
        self.vector_index = vector_index
        self.cell_store = cell_store
        self.session_id = session_id
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight
        self.embedding_client = embedding_client
        self.embed_fn = embed_fn

    def search(self, query: str, top_k: int = 5, fallback: bool = True) -> list[str]:
        """
        执行融合搜索。

        流程：
        1. 向量检索 + 关键词匹配 -> 融合排序
        2. 若 Top-1 融合分数 < 0.6 且允许 fallback，则触发 RRF 回退策略
        """
        fused = self._fusion_search(query, top_k)
        if fused and fused[0][1] >= 0.6:
            return [cell_id for cell_id, _ in fused[:top_k]]

        if not fallback:
            return [cell_id for cell_id, _ in fused[:top_k]]

        return self._fallback_search(query, top_k)

    def _embed_query(self, query: str) -> list[float] | None:
        """获取查询的 embedding 向量。"""
        if self.embed_fn is not None:
            return self.embed_fn(query)
        if self.embedding_client is not None:
            try:
                embeddings = self.embedding_client.embed([query])
                return embeddings[0] if embeddings else None
            except Exception as exc:
                logger.warning("Embedding failed for query: %s", exc)
                return None
        return None

    def _fusion_search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """标准融合搜索：向量 + 关键词。"""
        query_emb = self._embed_query(query)
        vector_results: list[tuple[str, float]] = []
        if query_emb is not None:
            try:
                vector_results = self.vector_index.search(query_emb, top_k=top_k * 2)
            except Exception as exc:
                logger.warning("Vector search failed: %s", exc)

        candidate_ids = {cell_id for cell_id, _ in vector_results}
        candidates = []
        for cid in candidate_ids:
            cell = self.cell_store.get(cid)
            if cell is not None:
                candidates.append(cell)

        vector_scores = {cell_id: math.exp(-dist) for cell_id, dist in vector_results}
        keyword_scores = self._keyword_scores(query, candidates)

        fused_scores: dict[str, float] = {}
        for cid in candidate_ids:
            v_score = vector_scores.get(cid, 0.0)
            k_score = keyword_scores.get(cid, 0.0)
            fused_scores[cid] = self.vector_weight * v_score + self.keyword_weight * k_score

        return sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)

    def _fallback_search(self, query: str, top_k: int) -> list[str]:
        """低置信度 fallback：放宽范围 + 全量关键词扫描 + RRF 合并。"""
        query_emb = self._embed_query(query)
        vector_results: list[tuple[str, float]] = []
        if query_emb is not None:
            try:
                vector_results = self.vector_index.search(query_emb, top_k=top_k * 3)
            except Exception as exc:
                logger.warning("Fallback vector search failed: %s", exc)

        # 全量关键词扫描（精确匹配）
        keyword_hits = self._exact_keyword_scan(query)

        # RRF 合并
        rrf_scores: dict[str, float] = defaultdict(float)
        for rank, (cell_id, _) in enumerate(vector_results, start=1):
            rrf_scores[cell_id] += 1.0 / (60 + rank)
        for rank, (cell_id, _) in enumerate(keyword_hits, start=1):
            rrf_scores[cell_id] += 1.0 / (60 + rank)

        sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        return [cell_id for cell_id, _ in sorted_results[:top_k]]

    def _keyword_scores(self, query: str, cells: list) -> dict[str, float]:
        """为每个 Cell 计算关键词匹配分数（Jaccard + 实体奖励）。"""
        query_tokens = set(query.lower().split())
        if not query_tokens:
            return {}

        scores: dict[str, float] = {}
        for cell in cells:
            cell_tokens: set[str] = set()
            for kw in cell.keywords or []:
                cell_tokens.add(kw.lower())
            for word in (cell.summary or "").lower().split():
                cell_tokens.add(word)

            intersection = query_tokens & cell_tokens
            union = query_tokens | cell_tokens
            jaccard = len(intersection) / len(union) if union else 0.0

            # 实体匹配奖励
            entity_bonus = 0.0
            cell_entities = {e.lower() for e in (cell.entities or [])}
            overlap = query_tokens & cell_entities
            if overlap:
                entity_bonus = min(0.3, 0.15 * len(overlap))

            scores[cell.id] = min(1.0, jaccard + entity_bonus)
        return scores

    def _exact_keyword_scan(self, query: str) -> list[tuple[str, float]]:
        """精确关键词扫描：匹配 summary 和 keywords，返回按匹配度排序的列表。"""
        query_tokens = query.lower().split()
        if not query_tokens:
            return []

        try:
            cells = self.cell_store.list_by_session(self.session_id, limit=100)
        except Exception as exc:
            logger.warning("Failed to list cells for keyword scan: %s", exc)
            return []

        scored: list[tuple[str, float]] = []
        for cell in cells:
            text = (cell.summary or "").lower()
            keywords = [k.lower() for k in (cell.keywords or [])]
            match_count = 0
            for token in query_tokens:
                if token in text or any(token in kw for kw in keywords):
                    match_count += 1
            if match_count > 0:
                score = match_count / len(query_tokens)
                scored.append((cell.id, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:30]
