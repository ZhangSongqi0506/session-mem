from __future__ import annotations


class RetrievalConfig:
    """检索策略可调节参数集中配置。"""

    # RRF 参数
    RRF_K: int = 60

    # 双路召回 top_k
    VECTOR_SEARCH_TOP_K: int = 50
    KEYWORD_SEARCH_TOP_K: int = 50

    # Fallback 扩大召回
    FALLBACK_VECTOR_SEARCH_TOP_K: int = 100
    FALLBACK_KEYWORD_SEARCH_TOP_K: int = 100

    # 向量检索分数阈值（math.exp(-dist) 必须 >= 此值）
    VECTOR_SCORE_THRESHOLD: float = 0.3

    # RRF fallback 触发阈值（Top-1 RRF score < 此值时扩大召回）
    RRF_FALLBACK_THRESHOLD: float = 0.015

    # MemorySystem 主阈值（RRF score >= 此值才进入 selected）
    MEMORY_SYSTEM_THRESHOLD: float = 0.015

    # 双路融合权重
    VECTOR_WEIGHT: float = 0.6
    KEYWORD_WEIGHT: float = 0.4

    # BM25 参数
    BM25_K1: float = 1.5
    BM25_B: float = 0.75
