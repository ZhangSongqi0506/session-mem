from __future__ import annotations

import json
from typing import Any


def safe_json_loads(text: str) -> dict[str, Any] | None:
    """
    安全解析 LLM 返回的 JSON。
    先尝试直接解析，若失败则尝试提取 markdown 代码块内容，
    再尝试用正则提取花括号内容，最后尝试逐行修复。
    """
    # 1. 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. 尝试提取 ```json ... ``` 或 ``` ... ``` 内容
    lines = text.splitlines()
    inside = False
    buffer: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if inside:
                break
            inside = True
            continue
        if inside:
            buffer.append(line)

    if buffer:
        try:
            return json.loads("\n".join(buffer))
        except json.JSONDecodeError:
            pass

    # 3. 尝试去掉首尾空白和多余字符后再解析
    cleaned = text.strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

    # 4. 尝试用正则提取最外层花括号内容
    import re

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # 5. 尝试去掉尾部逗号后解析（常见 LLM 错误：最后一个字段带逗号）
    no_trailing_comma = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        return json.loads(no_trailing_comma)
    except json.JSONDecodeError:
        pass

    return None
