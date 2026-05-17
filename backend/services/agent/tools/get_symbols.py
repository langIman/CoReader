"""Agent Tool：查询符号定义（函数、类、方法）。"""

from typing import Any

from backend.dao.database import get_connection
from backend.services.agent.tools.base import BaseTool


class GetSymbolsTool(BaseTool):
    """从 SQLite 查询项目中的符号定义。"""

    def __init__(self, project_name: str) -> None:
        self._project_name = project_name

    @property
    def name(self) -> str:
        return "get_symbols"

    @property
    def description(self) -> str:
        return (
            "查询项目中的函数、类、方法定义。"
            "可按文件路径或类型（function/class/method）过滤。"
            "返回 [{qualified_name, name, kind, file, line_start, line_end, params, docstring, is_entry}]。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "按文件路径过滤，如 'backend/services/file_service.py'",
                },
                "kind": {
                    "type": "string",
                    "enum": ["function", "async_function", "class", "method"],
                    "description": "按符号类型过滤",
                },
            },
            "required": [],
        }

    async def execute(self, *, file: str | None = None, kind: str | None = None, **kwargs: Any) -> Any:
        conn = get_connection()
        try:
            query = (
                "SELECT qualified_name, name, kind, file, line_start, line_end, "
                "params, docstring, is_entry FROM symbols WHERE project_name = ?"
            )
            params: list[Any] = [self._project_name]

            if file:
                query += " AND file = ?"
                params.append(file)
            if kind:
                query += " AND kind = ?"
                params.append(kind)

            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "qualified_name": r[0],
                    "name": r[1],
                    "kind": r[2],
                    "file": r[3],
                    "line_start": r[4],
                    "line_end": r[5],
                    "params": r[6],
                    "docstring": r[7],
                    "is_entry": bool(r[8]),
                }
                for r in rows
            ]
        finally:
            conn.close()
