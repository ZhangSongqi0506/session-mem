from __future__ import annotations

from session_mem.core.cell import MemoryCell
from session_mem.llm.base import LLMClient
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
        # TODO: Phase 4 实现 LLM Prompt 调用与全量融合逻辑
        raise NotImplementedError("MetaCellGenerator.generate() 待实现")
