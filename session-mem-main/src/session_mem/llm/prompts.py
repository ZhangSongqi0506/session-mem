"""LLM Prompt 模板：语义边界检测 + Cell 生成。"""

from typing import Any

# ============================================================
# 语义边界检测（独立新会话调用）
# ============================================================
SEMANTIC_BOUNDARY_SYSTEM = """\
你是一个对话语义边界检测器。请分析以下对话，判断其中存在几个独立的话题单元，并输出切分点索引。

规则：
1. 切分点索引表示在该轮次**之后**进行切分。例如 [3, 6] 表示第 3 轮后和第 6 轮后各有一个边界。
2. 若对话语义连贯、无话题转折，输出空列表 []。
3. 若只有一处转折，输出单个索引，如 [4]。
4. 若有多个转折，输出多个索引，按从小到大排列。
5. 最后一个切分点之后的轮次将保留在 Buffer 中继续累积，因此通常不把最后一轮作为切分点，除非最后一部分本身已构成一个完整单元。

输出格式（严格 JSON）：
{"split_indices": [3, 6]}
"""


def build_boundary_prompt(turns: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    构建语义边界检测的独立新会话 messages。
    turns: 当前 SenMemBuffer 内的所有轮次，格式 {"role": "user"/"assistant", "content": "..."}
    """
    messages = [{"role": "system", "content": SEMANTIC_BOUNDARY_SYSTEM}]
    messages.extend(turns)
    messages.append(
        {
            "role": "user",
            "content": "请分析上述对话的语义边界，输出 JSON 格式的 split_indices 列表：",
        }
    )
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
        cells: 本次新产生的全部普通 Cell 字典列表（1 个或多个）。
        previous_meta: 上一个版本的 Meta Cell 字典，若为 None 则生成初始版本。
    """
    cell_texts = "\n\n".join([f"Cell (ID: {c['id']}):\n{c.get('raw_text', '')}" for c in cells])
    if previous_meta:
        user_content = (
            f"已有 Meta Cell 全文:\n{previous_meta.get('raw_text', '')}\n\n"
            f"新产生的 Memory Cell 列表:\n{cell_texts}\n\n"
            "请基于已有 Meta Cell 和上述新 Cell，增量融合重写 Meta Cell JSON："
        )
    else:
        user_content = f"当前 Memory Cell 列表:\n{cell_texts}\n\n" "请生成初始 Meta Cell JSON："
    return [
        {"role": "system", "content": META_CELL_GENERATION_SYSTEM},
        {"role": "user", "content": user_content},
    ]
