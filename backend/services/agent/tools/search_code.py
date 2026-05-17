"""Agent Tool：正则/关键词搜索源码。"""

from __future__ import annotations

import re
from typing import Any

from backend.dao.project_file_persist import load_project_files
from backend.services.agent.tools.base import BaseTool


class SearchCodeTool(BaseTool):

    def __init__(self, project_name: str = "") -> None:
        self._project_name = project_name

    @property
    def name(self) -> str:
        return "search_code"

    @property
    def description(self) -> str:
        return (
            "正则或关键词搜索项目源码，类似 grep。"
            "返回匹配行的文件 + 行号 + 片段（含上下各 1 行）。"
            "用于查字符串字面量、注释、非符号命中（如配置 key、错误信息）。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Python 正则或纯字符串",
                },
                "is_regex": {
                    "type": "boolean",
                    "description": "true 表示 pattern 是正则，false 表示纯字符串（默认）",
                    "default": False,
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多返回条数，默认 20，最大 50",
                    "default": 20,
                    "maximum": 50,
                },
            },
            "required": ["pattern"],
        }

    async def execute(
        self,
        *,
        pattern: str,
        is_regex: bool = False,
        max_results: int = 20,
        **kwargs: Any,
    ) -> Any:
        project_files = load_project_files(self._project_name)
        if not project_files:
            return {"error": "没有已加载的项目"}

        max_results = max(1, min(max_results, 50))

        if is_regex:
            try:
                rx = re.compile(pattern)
            except re.error as e:
                return {"error": f"正则编译失败: {e}"}
            matcher = lambda line: rx.search(line) is not None
        else:
            needle = pattern
            matcher = lambda line: needle in line

        results: list[dict] = []
        for path in sorted(project_files.keys()):
            content = project_files[path]
            lines = content.splitlines()
            for i, line in enumerate(lines):
                if not matcher(line):
                    continue
                snippet_start = max(0, i - 3)       # ±3 行上下文（原为 ±1）
                snippet_end = min(len(lines), i + 4)
                snippet = "\n".join(lines[snippet_start:snippet_end])
                results.append({
                    "file": path,
                    "line": i + 1,           # 1-indexed
                    "snippet": snippet,
                    "start_line": snippet_start + 1,  # 便于 LLM 直接传给 get_file_content
                })
                if len(results) >= max_results:
                    return {
                        "pattern": pattern,
                        "is_regex": is_regex,
                        "truncated": True,
                        "results": results,
                    }

        return {
            "pattern": pattern,
            "is_regex": is_regex,
            "truncated": False,
            "results": results,
        }
