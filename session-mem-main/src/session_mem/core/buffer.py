from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from session_mem.core.cell import MemoryCell


@dataclass
class Turn:
    """单轮对话，带时间戳。"""

    role: str  # 'user' | 'assistant'
    content: str
    timestamp: str  # ISO 8601 UTC


class SenMemBuffer:
    """
    感觉缓冲（SenMemBuffer）：原始对话的保真暂存池与智能切分闸门。
    """

    def __init__(
        self,
        session_id: str,
        soft_limit: int = 512,
        hard_limit: int = 2048,
        gap_threshold_minutes: int = 30,
    ):
        self.session_id = session_id
        self.soft_limit = soft_limit
        self.hard_limit = hard_limit
        self.gap_threshold_minutes = gap_threshold_minutes
        self.turns: list[Turn] = []
        self._token_estimator: Callable[[str], int] | None = None
        self._check_count = 0

    def set_token_estimator(self, estimator: Callable[[str], int]) -> None:
        self._token_estimator = estimator

    def add_turn(self, turn: Turn) -> None:
        self.turns.append(turn)

    def estimated_tokens(self) -> int:
        if self._token_estimator is None:
            return sum(len(t.content) for t in self.turns)
        return sum(self._token_estimator(t.content) for t in self.turns)

    def should_trigger_check(self) -> bool:
        """当 token 数首次跨越 512 的整数倍且未达硬上限时触发检测。"""
        tokens = self.estimated_tokens()
        if tokens >= self.hard_limit:
            return False
        next_threshold = self.soft_limit * (self._check_count + 1)
        if tokens >= next_threshold:
            self._check_count += 1
            return True
        return False

    def is_hard_limit_reached(self) -> bool:
        return self.estimated_tokens() >= self.hard_limit

    def gap_detected(self) -> bool:
        """检测相邻两轮时间差是否超过阈值。"""
        if len(self.turns) < 2:
            return False
        try:
            t_prev = self.turns[-2].timestamp.replace("Z", "+00:00")
            t_curr = self.turns[-1].timestamp.replace("Z", "+00:00")
            dt_prev = datetime.fromisoformat(t_prev)
            dt_curr = datetime.fromisoformat(t_curr)
            diff_minutes = (dt_curr - dt_prev).total_seconds() / 60
            return diff_minutes > self.gap_threshold_minutes
        except Exception:
            return False

    def extract_for_cell(self, cutoff_index: int) -> list[Turn]:
        """提取前 cutoff_index 轮用于生成 Cell，剩余留在 Buffer。"""
        cell_turns = self.turns[:cutoff_index]
        self.turns = self.turns[cutoff_index:]
        self._check_count = 0
        return cell_turns

    def raw_text(self) -> str:
        return "\n".join(f"[{t.role}]: {t.content}" for t in self.turns)


class ShortMemBuffer:
    """
    短期缓冲（ShortMemBuffer）：Cell 摘要的统一检索池。
    MVP 阶段不区分活跃/存储窗口，所有 Cell 均参与检索。
    """

    def __init__(self) -> None:
        self.cells: list[MemoryCell] = []

    def add(self, cell: MemoryCell) -> None:
        self.cells.append(cell)

    def all_cells(self) -> list[MemoryCell]:
        return self.cells

    def get(self, cell_id: str) -> MemoryCell | None:
        for c in self.cells:
            if c.id == cell_id:
                return c
        return None
