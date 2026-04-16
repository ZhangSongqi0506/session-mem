from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class LoCoMoSession:
    """表示一条 LoCoMo 拼接会话（多个原始 session 合并为一个长会话）。"""

    def __init__(
        self,
        session_id: str,
        turns: list[dict[str, str]],
        qa_list: list[dict[str, Any]],
    ):
        self.session_id = session_id
        self.turns = turns
        self.qa_list = qa_list

    @property
    def turn_count(self) -> int:
        return len(self.turns)


def _parse_session_datetime(date_str: str | None) -> datetime:
    """从 LoCoMo 日期字符串解析为 datetime，失败时返回当前 UTC 时间。"""
    if not date_str:
        return datetime.now(timezone.utc)
    # 常见格式: "7 May 2023" 或 "May 7, 2023"
    for fmt in ("%d %B %Y", "%B %d, %Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _build_timestamp(session_dt: datetime, turn_index: int) -> str:
    """为每个 turn 生成 ISO 8601 时间戳，同一 session 内按分钟递增。"""
    dt = session_dt + timedelta(minutes=turn_index)
    return dt.isoformat().replace("+00:00", "Z")


def _normalize_role(speaker: str, speaker_a: str, speaker_b: str) -> str:
    """保留原始 speaker 名称，不再强制映射为 user/assistant。"""
    return speaker.strip()


def load_locomo_sessions(
    data_path: str | Path,
    max_sessions: int | None = None,
    max_turns: int | None = None,
    max_qa_per_session: int | None = None,
) -> list[LoCoMoSession]:
    """
    从 LoCoMo JSON 文件加载数据，将同一 conversation 的多个 session 合并为一个长会话。

    Args:
        data_path: LoCoMo JSON 文件路径（如 locomo10.json）
        max_sessions: 最多加载多少个 conversation
        max_turns: 每个合并会话最多保留多少轮对话
        max_qa_per_session: 每个合并会话最多评估多少个 QA
    """
    path = Path(data_path)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    if not isinstance(raw_data, list):
        raise ValueError("Expected a JSON array of conversations")

    sessions: list[LoCoMoSession] = []
    for conv_idx, item in enumerate(raw_data):
        if max_sessions is not None and len(sessions) >= max_sessions:
            break

        sample_id = item.get("sample_id") or f"conv_{conv_idx}"
        conversation = item.get("conversation", {})
        qa_list = item.get("qa", [])

        speaker_a = conversation.get("speaker_a", "SpeakerA")
        speaker_b = conversation.get("speaker_b", "SpeakerB")

        # 提取所有 session_x，按数字排序
        session_keys = sorted(
            [k for k in conversation.keys() if re.fullmatch(r"session_(\d+)", k)],
            key=lambda k: int(k.split("_")[1]),
        )

        turns: list[dict[str, str]] = []
        for session_key in session_keys:
            session_turns = conversation.get(session_key, [])
            if not isinstance(session_turns, list):
                continue

            date_time_key = f"{session_key}_date_time"
            date_str = conversation.get(date_time_key, "")
            session_dt = _parse_session_datetime(date_str)

            for turn_idx, turn in enumerate(session_turns):
                text = turn.get("text", "")
                if not text:
                    continue
                speaker = turn.get("speaker", speaker_a)
                role = _normalize_role(speaker, speaker_a, speaker_b)
                timestamp = _build_timestamp(session_dt, turn_idx)
                turns.append(
                    {
                        "role": role,
                        "content": text,
                        "timestamp": timestamp,
                    }
                )

        if max_turns is not None:
            turns = turns[:max_turns]

        qa_subset = qa_list
        if max_qa_per_session is not None:
            qa_subset = qa_list[:max_qa_per_session]

        sessions.append(
            LoCoMoSession(
                session_id=sample_id,
                turns=turns,
                qa_list=qa_subset,
            )
        )

    return sessions
