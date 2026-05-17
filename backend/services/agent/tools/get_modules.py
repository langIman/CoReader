"""Agent Tool：查询模块（文件）级依赖。"""

from typing import Any

from backend.dao.database import get_connection
from backend.services.agent.tools.base import BaseTool


class GetModulesTool(BaseTool):
    """从 SQLite 查询项目中的模块信息及 import 依赖。"""

    def __init__(self, project_name: str) -> None:
        self._project_name = project_name

    @property
    def name(self) -> str:
        return "get_modules"

    @property
    def description(self) -> str:
        return (
            "查询项目中的模块（文件）信息，包括行数、符号数和 import 依赖。"
            "可按文件路径过滤。"
            "返回 [{path, line_count, symbol_count, imports}]。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "按文件路径过滤，如 'backend/services/file_service.py'",
                },
            },
            "required": [],
        }

    async def execute(self, *, path: str | None = None, **kwargs: Any) -> Any:
        conn = get_connection()
        try:
            query = (
                "SELECT path, line_count, symbol_count, imports "
                "FROM modules WHERE project_name = ?"
            )
            params: list[Any] = [self._project_name]

            if path:
                query += " AND path = ?"
                params.append(path)

            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "path": r[0],
                    "line_count": r[1],
                    "symbol_count": r[2],
                    "imports": r[3],
                }
                for r in rows
            ]
        finally:
            conn.close()
