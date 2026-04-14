"""LLM Prompt 模板：语义边界检测 + Cell 生成。"""

from typing import Any

# ============================================================
# 语义边界检测（独立新会话调用）
# ============================================================
SEMANTIC_BOUNDARY_SYSTEM = """\
你是一个对话语义边界检测器。请判断以下对话是否出现了明显的话题转折、任务结束或意图切换。
只输出以下两种标签之一，不要解释：
- CONTINUE：对话语义连贯，应继续累积
- SPLIT：出现话题转折/任务结束/意图切换，应在当前轮次前切分
"""


def build_boundary_prompt(turns: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    构建语义边界检测的独立新会话 messages。
    turns: 当前 SenMemBuffer 内的所有轮次，格式 {"role": "user"/"assistant", "content": "..."}
    """
    messages = [{"role": "system", "content": SEMANTIC_BOUNDARY_SYSTEM}]
    messages.extend(turns)
    messages.append({"role": "user", "content": "请判断上述对话是否应切分："})
    return messages


# ============================================================
# Cell 生成
# ============================================================
CELL_GENERATION_SYSTEM = """\
你是一个对话记忆结构化专家。请将以下对话内容整理为一个 JSON 格式的 Memory Cell。

要求：
1. summary: 30-50 tokens，概括核心内容
2. keywords: 5-8 个关键词（list）
3. entities: 3-5 个关键实体（list）
4. cell_type: 从 fact / constraint / preference / task 中选一
5. confidence: 0-1 之间的 float，表示你对摘要质量的自信程度
6. causal_deps: 若对话中明确提到之前某个约束/事实，列出其 Cell ID（list，没有则空）

必须输出合法 JSON，不要 markdown 代码块包围，不要额外说明。
"""

CELL_GENERATION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "memory_cell",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "entities": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "cell_type": {
                    "type": "string",
                    "enum": ["fact", "constraint", "preference", "task"],
                },
                "confidence": {"type": "number"},
                "causal_deps": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "summary",
                "keywords",
                "entities",
                "cell_type",
                "confidence",
                "causal_deps",
            ],
            "additionalProperties": False,
        },
    },
}


def build_cell_generation_prompt(raw_text: str) -> list[dict[str, str]]:
    messages = [
        {"role": "system", "content": CELL_GENERATION_SYSTEM},
        {"role": "user", "content": f"对话内容：\n{raw_text}\n\n请生成 Memory Cell JSON："},
    ]
    return messages


# ============================================================
# Meta Cell 生成 / 更新
# ============================================================
META_CELL_GENERATION_SYSTEM = """\
你是一个会话主旨摘要专家。请根据已生成的 Memory Cell 列表，提炼出一段会话级全局摘要（Meta Cell）。

要求：
1. summary: 概括会话的核心目标、当前进度和关键约束。长度自由，以准确传达当前全局状态为准，不截断
2. keywords: 5-8 个关键词（list）
3. entities: 3-5 个关键实体（list）
4. confidence: 0-1 之间的 float
5. causal_deps: 若有明确的跨 Cell 依赖，列出其 Cell ID（list，没有则空）

必须输出合法 JSON，不要 markdown 代码块包围，不要额外说明。
"""

META_CELL_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "meta_cell",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "entities": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "confidence": {"type": "number"},
                "causal_deps": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "summary",
                "keywords",
                "entities",
                "confidence",
                "causal_deps",
            ],
            "additionalProperties": False,
        },
    },
}


def build_meta_cell_prompt(
    cells: list[dict[str, Any]],
    previous_meta: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """构建 Meta Cell 生成的 prompt。

    Args:
        cells: 当前会话全部普通 Cell 的字典列表（按时间序）。
        previous_meta: 上一个版本的 Meta Cell 字典，若为 None 则生成初始版本。
    """
    cells_text = "\n\n".join(
        f"Cell {i + 1} (ID: {c['id']}):\nSummary: {c.get('summary', '')}\n"
        f"Keywords: {c.get('keywords', [])}\nEntities: {c.get('entities', [])}\n"
        f"Type: {c.get('cell_type', 'fact')}\nRaw: {c.get('raw_text', '')}"
        for i, c in enumerate(cells)
    )
    if previous_meta:
        user_content = (
            f"已有 Meta Cell 全文:\n{previous_meta.get('raw_text', '')}\n\n"
            f"当前全部普通 Cell:\n{cells_text}\n\n"
            "请全量融合重写 Meta Cell JSON："
        )
    else:
        user_content = f"当前全部普通 Cell:\n{cells_text}\n\n请生成初始 Meta Cell JSON："
    return [
        {"role": "system", "content": META_CELL_GENERATION_SYSTEM},
        {"role": "user", "content": user_content},
    ]
