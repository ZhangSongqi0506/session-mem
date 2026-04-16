from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Callable

from session_mem.config import RetrievalConfig
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
        """执行融合搜索并返回前 top_k 个 cell_id。"""
        results = self.search_with_scores(query, fallback=fallback)
        return [cell_id for cell_id, _ in results[:top_k]]

    def search_with_scores(self, query: str, fallback: bool = True) -> list[tuple[str, float]]:
        """执行双路独立召回 + RRF 融合，返回全部候选及其 RRF 分数。"""
        v_results = self._vector_search(query, RetrievalConfig.VECTOR_SEARCH_TOP_K)
        k_results = self._keyword_search(query, RetrievalConfig.KEYWORD_SEARCH_TOP_K)
        fused = self._rrf_fuse(v_results, k_results)

        if fused and fused[0][1] >= RetrievalConfig.RRF_FALLBACK_THRESHOLD:
            return fused

        if not fallback:
            return fused

        # Fallback: 扩大两路召回范围后重新 RRF
        v_results_fb = self._vector_search(query, RetrievalConfig.FALLBACK_VECTOR_SEARCH_TOP_K)
        k_results_fb = self._keyword_search(query, RetrievalConfig.FALLBACK_KEYWORD_SEARCH_TOP_K)
        return self._rrf_fuse(v_results_fb, k_results_fb)

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

    def _vector_search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """向量检索并按阈值过滤，返回按 vector_score 降序的列表。"""
        query_emb = self._embed_query(query)
        if query_emb is None:
            return []

        try:
            raw_results = self.vector_index.search(query_emb, top_k=top_k)
        except Exception as exc:
            logger.warning("Vector search failed: %s", exc)
            return []

        scored: list[tuple[str, float]] = []
        for cell_id, dist in raw_results:
            score = math.exp(-dist)
            if score >= RetrievalConfig.VECTOR_SCORE_THRESHOLD:
                scored.append((cell_id, score))
        return scored

    def _keyword_search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """关键词独立召回，返回按 keyword_score 降序的列表。"""
        try:
            cells = self.cell_store.list_by_session(self.session_id, limit=100)
        except Exception as exc:
            logger.warning("Failed to list cells for keyword search: %s", exc)
            return []

        scores = self.keyword_scores(query, cells)
        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        filtered = [(cid, score) for cid, score in sorted_results if score > 0]
        return filtered[:top_k]

    def _rrf_fuse(
        self,
        v_results: list[tuple[str, float]],
        k_results: list[tuple[str, float]],
    ) -> list[tuple[str, float]]:
        """使用 RRF 公式融合向量路与关键词路的排名结果。"""
        rrf_scores: dict[str, float] = defaultdict(float)
        for rank, (cell_id, _) in enumerate(v_results, start=1):
            rrf_scores[cell_id] += 1.0 / (RetrievalConfig.RRF_K + rank)
        for rank, (cell_id, _) in enumerate(k_results, start=1):
            rrf_scores[cell_id] += 1.0 / (RetrievalConfig.RRF_K + rank)

        # Tie-breaker: 取该 cell 在两路中的最高原始分数
        orig_scores: dict[str, float] = {}
        for cell_id, score in v_results:
            orig_scores[cell_id] = max(orig_scores.get(cell_id, 0.0), score)
        for cell_id, score in k_results:
            orig_scores[cell_id] = max(orig_scores.get(cell_id, 0.0), score)

        return sorted(
            rrf_scores.items(),
            key=lambda x: (-x[1], -orig_scores.get(x[0], 0.0), x[0]),
        )

    def keyword_scores(self, query: str, cells: list) -> dict[str, float]:
        """为每个 Cell 计算关键词匹配分数（BM25 + 实体奖励）。"""
        query_tokens = query.lower().split()
        if not query_tokens:
            return {}

        unique_query_tokens = set(query_tokens)

        # 构建文档（cell）的 token 列表
        cell_docs: dict[str, list[str]] = {}
        for cell in cells:
            doc_tokens: list[str] = []
            for kw in cell.keywords or []:
                doc_tokens.append(kw.lower())
            for word in (cell.raw_text or "").lower().split():
                doc_tokens.append(word)
            cell_docs[cell.id] = doc_tokens

        # 计算 session-level IDF
        N = len(cells)
        df: dict[str, int] = defaultdict(int)
        for doc_tokens in cell_docs.values():
            seen_in_doc = set(doc_tokens)
            for token in seen_in_doc:
                df[token] += 1

        idf: dict[str, float] = {}
        for token, freq in df.items():
            idf[token] = math.log((N - freq + 0.5) / (freq + 0.5) + 1.0)

        # 计算平均文档长度
        doc_lengths = [len(tokens) for tokens in cell_docs.values()]
        avgdl = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 1.0
        if avgdl == 0:
            avgdl = 1.0

        k1 = RetrievalConfig.BM25_K1
        b = RetrievalConfig.BM25_B

        scores: dict[str, float] = {}
        for cell in cells:
            doc_tokens = cell_docs[cell.id]
            doc_len = len(doc_tokens)
            tf_map: dict[str, int] = defaultdict(int)
            for token in doc_tokens:
                tf_map[token] += 1

            bm25_score = 0.0
            for token in unique_query_tokens:
                tf = tf_map.get(token, 0)
                if tf == 0:
                    continue
                denom = tf + k1 * (1 - b + b * (doc_len / avgdl))
                bm25_score += idf.get(token, 0.0) * (tf * (k1 + 1)) / denom

            # 实体匹配奖励
            entity_bonus = 0.0
            cell_entities = {e.lower() for e in (cell.entities or [])}
            overlap = unique_query_tokens & cell_entities
            if overlap:
                entity_bonus = min(0.3, 0.15 * len(overlap))

            scores[cell.id] = bm25_score + entity_bonus
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
