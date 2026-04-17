from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from typing import Callable

from session_mem.config import RetrievalConfig
from session_mem.llm.base import LLMClient
from session_mem.storage.base import CellStore, VectorIndex

logger = logging.getLogger(__name__)

# 中英文常用停用词
_STOPWORDS = {
    # English
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "shall",
    "can",
    "need",
    "to",
    "of",
    "in",
    "for",
    "on",
    "with",
    "at",
    "by",
    "from",
    "as",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "between",
    "under",
    "again",
    "further",
    "then",
    "once",
    "here",
    "there",
    "when",
    "where",
    "why",
    "how",
    "all",
    "each",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "no",
    "nor",
    "not",
    "only",
    "own",
    "same",
    "so",
    "than",
    "too",
    "very",
    "just",
    "and",
    "but",
    "or",
    "yet",
    "if",
    "because",
    "although",
    "though",
    "while",
    "since",
    "until",
    "unless",
    "whether",
    "either",
    "neither",
    "both",
    "also",
    "much",
    "many",
    "little",
    "less",
    "least",
    "quite",
    "rather",
    "enough",
    "even",
    "still",
    "already",
    "almost",
    "indeed",
    "thus",
    "hence",
    "therefore",
    "however",
    "nevertheless",
    "moreover",
    "furthermore",
    "besides",
    "otherwise",
    "instead",
    "accordingly",
    "consequently",
    "subsequently",
    "eventually",
    "finally",
    "initially",
    "originally",
    "previously",
    "formerly",
    "lately",
    "recently",
    "soon",
    "immediately",
    "instantly",
    "directly",
    "briefly",
    "quickly",
    "slowly",
    "gradually",
    "suddenly",
    "ultimately",
    "this",
    "that",
    "these",
    "those",
    "i",
    "you",
    "he",
    "she",
    "it",
    "we",
    "they",
    "them",
    "his",
    "her",
    "its",
    "our",
    "their",
    "my",
    "your",
    "him",
    "us",
    "me",
    # Chinese
    "的",
    "了",
    "在",
    "是",
    "我",
    "你",
    "他",
    "她",
    "它",
    "这",
    "那",
    "有",
    "和",
    "与",
    "及",
    "或",
    "但",
    "而",
    "因",
    "为",
    "之",
    "其",
    "个",
    "们",
    "等",
    "很",
    "都",
    "也",
    "就",
    "不",
    "会",
    "要",
    "能",
    "可",
    "上",
    "下",
    "中",
    "大",
    "小",
    "来",
    "去",
    "过",
    "到",
    "从",
    "向",
    "把",
    "被",
    "让",
    "给",
    "对",
    "将",
    "还",
    "说",
    "进行",
    "通过",
    "根据",
    "关于",
    "没有",
    "已经",
    "正在",
    "可以",
    "应该",
    "需要",
    "认为",
    "使用",
    "做",
    "出",
    "想",
    "看",
    "见",
    "得",
    "着",
    "比",
    "更",
    "最",
    "太",
    "非常",
    "比较",
    "一下",
    "一个",
    "一种",
    "这些",
    "那些",
    "什么",
    "怎么",
    "怎样",
    "谁",
    "哪",
    "哪儿",
    "哪里",
    "多少",
    "几",
    "为什么",
    "如何",
    "时候",
    "地方",
    "东西",
    "事情",
    "问题",
    "情况",
    "方面",
    "部分",
    "其他",
    "另外",
    "其余",
    "任何",
    "所有",
    "一切",
    "每个",
    "各种",
    "各位",
    "大家",
    "我们",
    "你们",
    "他们",
    "她们",
    "它们",
    "这里",
    "那里",
    "这边",
    "那边",
    "此时",
    "此刻",
    "当时",
    "那时",
    "现在",
    "过去",
    "未来",
    "以前",
    "以后",
    "之后",
    "之前",
    "然后",
    "接着",
    "随后",
    "期间",
}


def _clean_token(token: str) -> str:
    """去除 token 中的标点符号并转小写。"""
    return re.sub(r"[^\w\s]", "", token).lower()


class HybridSearcher:
    """双路召回：向量相似度 + 关键词桥接，支持低置信度 fallback。"""

    def __init__(
        self,
        vector_index: VectorIndex,
        cell_store: CellStore,
        session_id: str,
        vector_weight: float | None = None,
        keyword_weight: float | None = None,
        embedding_client: LLMClient | None = None,
        embed_fn: Callable[[str], list[float] | None] | None = None,
    ):
        self.vector_index = vector_index
        self.cell_store = cell_store
        self.session_id = session_id
        self.vector_weight = (
            vector_weight if vector_weight is not None else RetrievalConfig.VECTOR_WEIGHT
        )
        self.keyword_weight = (
            keyword_weight if keyword_weight is not None else RetrievalConfig.KEYWORD_WEIGHT
        )
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
        query_tokens = [
            _clean_token(t)
            for t in query.split()
            if _clean_token(t) and _clean_token(t) not in _STOPWORDS
        ]
        if not query_tokens:
            return {}

        unique_query_tokens = set(query_tokens)

        # 构建文档（cell）的 token 列表
        cell_docs: dict[str, list[str]] = {}
        for cell in cells:
            doc_tokens: list[str] = []
            for kw in cell.keywords or []:
                doc_tokens.append(_clean_token(kw))
            for word in (cell.raw_text or "").split():
                doc_tokens.append(_clean_token(word))
            cell_docs[cell.id] = doc_tokens

        # 计算 session-level IDF
        N = len(cells)
        df: dict[str, int] = defaultdict(int)
        for doc_tokens in cell_docs.values():
            seen_in_doc = set(doc_tokens)
            for token in seen_in_doc:
                if token:
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

            # 实体匹配奖励（对原始实体也做清洗后匹配）
            entity_bonus = 0.0
            cell_entities = {_clean_token(e) for e in (cell.entities or [])}
            overlap = unique_query_tokens & cell_entities
            if overlap:
                entity_bonus = min(0.3, 0.15 * len(overlap))

            total_score = bm25_score + entity_bonus
            if total_score > 0:
                scores[cell.id] = total_score
        return scores

    def _exact_keyword_scan(self, query: str) -> list[tuple[str, float]]:
        """精确关键词扫描：匹配 summary 和 keywords，返回按匹配度排序的列表。"""
        query_tokens = [
            t for t in {_clean_token(t) for t in query.split()} if t and t not in _STOPWORDS
        ]
        if not query_tokens:
            return []

        try:
            cells = self.cell_store.list_by_session(self.session_id, limit=100)
        except Exception as exc:
            logger.warning("Failed to list cells for keyword scan: %s", exc)
            return []

        scored: list[tuple[str, float]] = []
        for cell in cells:
            text = _clean_token(cell.summary or "")
            keywords = [_clean_token(k) for k in (cell.keywords or [])]
            match_count = 0
            for token in query_tokens:
                if token in text or any(token in kw for kw in keywords):
                    match_count += 1
            if match_count > 0:
                score = match_count / len(query_tokens)
                scored.append((cell.id, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:30]
