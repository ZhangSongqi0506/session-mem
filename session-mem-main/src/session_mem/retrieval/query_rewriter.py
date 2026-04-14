from __future__ import annotations

from session_mem.llm.base import LLMClient


class QueryRewriter:
    """查询重写：指代消解 + 伪文档扩展。"""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm = llm_client

    def rewrite(self, query: str, hot_zone: list[str]) -> str:
        """
        若查询过短或含指代词，尝试扩展；否则原样返回。
        """
        if len(query) >= 10 and not any(w in query for w in ("这", "那", "刚才", "之前")):
            return query

        if self.llm is None:
            return query

        context = "\n".join(hot_zone)
        messages = [
            {
                "role": "system",
                "content": "你是一个查询重写助手。请根据最近对话上下文，将用户的短查询或含指代词的查询扩展为明确、完整的句子。只输出扩展后的查询，不要解释。",
            },
            {
                "role": "user",
                "content": f"最近对话：\n{context}\n\n用户查询：{query}\n\n扩展后查询：",
            },
        ]
        return self.llm.isolated_chat(messages, temperature=0.2)
