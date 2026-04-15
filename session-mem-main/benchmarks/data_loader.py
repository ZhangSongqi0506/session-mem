from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class LoCoMoSession:
    """表示一条 LoCoMo 拼接会话。"""

    def __init__(
        self,
        session_id: str,
        turns: list[dict[str, str]],
        question: str | None = None,
        answer: str | None = None,
    ):
        self.session_id = session_id
        self.turns = turns
        self.question = question
        self.answer = answer

    @property
    def turn_count(self) -> int:
        return len(self.turns)


def load_locomo_sessions(
    data_path: str | Path,
    max_sessions: int | None = None,
    max_turns: int | None = None,
    role_field: str = "role",
    content_field: str = "content",
    timestamp_field: str = "timestamp",
    speaker_field: str | None = None,
    text_field: str | None = None,
) -> list[LoCoMoSession]:
    """
    从 JSON/JSONL 文件加载 LoCoMo 会话数据。

    支持两种格式：
    1. JSON 数组：每个元素是一条 session
    2. JSONL：每行是一条 session

    每条 session 期望包含：
    - session_id (str)
    - turns (list[dict])，每个 turn 至少包含 role/content/timestamp
    - 可选：question, answer

    若原始数据使用 speaker/text 而非 role/content，可通过 speaker_field/text_field 指定。
    """
    path = Path(data_path)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    raw_items: list[dict[str, Any]] = []
    if path.suffix.lower() == ".jsonl":
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    raw_items.append(json.loads(line))
    else:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                raw_items = data
            elif isinstance(data, dict) and "sessions" in data:
                raw_items = data["sessions"]
            else:
                raw_items = [data]

    sessions: list[LoCoMoSession] = []
    for idx, item in enumerate(raw_items):
        if max_sessions is not None and len(sessions) >= max_sessions:
            break

        sid = item.get("session_id") or item.get("id") or f"locomo_{idx}"
        turns_raw = item.get("turns") or item.get("messages") or item.get("conversation") or []
        if not turns_raw:
            continue

        turns: list[dict[str, str]] = []
        for t in turns_raw:
            role = t.get(role_field, "")
            content = t.get(content_field, "")
            timestamp = t.get(timestamp_field, "")

            # fallback: speaker -> role, text -> content
            if not role and speaker_field:
                role = t.get(speaker_field, "")
            if not content and text_field:
                content = t.get(text_field, "")

            # normalize role
            role = (role or "user").lower().strip()
            if role in ("user", "human", "customer"):
                role = "user"
            elif role in ("assistant", "agent", "bot", "system"):
                role = "assistant"

            if content:
                turns.append(
                    {
                        "role": role,
                        "content": content,
                        "timestamp": timestamp or "",
                    }
                )

        if max_turns is not None:
            turns = turns[:max_turns]

        sessions.append(
            LoCoMoSession(
                session_id=sid,
                turns=turns,
                question=item.get("question") or item.get("query"),
                answer=item.get("answer") or item.get("ground_truth"),
            )
        )

    return sessions
