from __future__ import annotations

from session_mem.storage.base import CellStore, VectorIndex


class HybridSearcher:
    """双路召回：向量相似度 + 关键词桥接。"""

    def __init__(
        self,
        vector_index: VectorIndex,
        cell_store: CellStore,
        session_id: str,
        vector_weight: float = 0.75,
        keyword_weight: float = 0.25,
    ):
        self.vector_index = vector_index
        self.cell_store = cell_store
        self.session_id = session_id
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight

    def search(self, query: str, top_k: int = 5) -> list[str]:
        # TODO: 实现向量化查询 + 关键词匹配融合
        # 1. query -> embedding -> vector search
        # 2. query keywords -> Jaccard + entity match
        # 3. fusion score -> rank -> return cell_ids
        return []
