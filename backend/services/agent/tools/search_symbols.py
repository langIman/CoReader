"""Agent Tool：BM25 语义搜索符号。"""

from __future__ import annotations

from typing import Any

from backend.services.agent.tools.base import BaseTool
from backend.services.qa.retrieval import retrieve_symbols_for_question


class SearchSymbolsTool(BaseTool):

    def __init__(self, project_name: str) -> None:
        self._project_name = project_name

    @property
    def name(self) -> str:
        return "search_symbols"

    @property
    def description(self) -> str:
        return (
            "按关键词语义搜索项目符号（函数/类）。返回 Top-K 匹配的符号，"
            "含 file 和 line 范围。用于定位要读的代码。"
            "比 get_symbols 更适合用户问题抽象时——只有关键词，不知道具体文件。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "自然语言或关键词，如 '加载配置' / 'load_config'",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回 Top-K，默认 10，最大 20",
                    "default": 10,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        }

    async def execute(self, *, query: str, top_k: int = 10, **kwargs: Any) -> Any:
        top_k = max(1, min(top_k, 20))
        hits = retrieve_symbols_for_question(self._project_name, query, k=top_k)
        if not hits:
            return {"query": query, "results": [], "note": "未命中任何符号"}
        return [
            {
                "qualified_name": s.qualified_name,
                "name": s.name,
                "kind": s.kind,
                "file": s.file,
                "line_start": s.line_start,
                "line_end": s.line_end,
                "docstring": s.docstring,
            }
            for s in hits
        ]
