"""Agent Tool：查询函数调用关系。"""

from typing import Any

from backend.dao.database import get_connection
from backend.services.agent.tools.base import BaseTool


class GetCallEdgesTool(BaseTool):
    """从 SQLite 查询项目中的函数调用关系。"""

    def __init__(self, project_name: str) -> None:
        self._project_name = project_name

    @property
    def name(self) -> str:
        return "get_call_edges"

    @property
    def description(self) -> str:
        return (
            "查询项目中的函数调用关系。"
            "可按调用者(caller)或被调用者(callee)过滤。"
            "返回 [{caller, callee_name, callee_resolved, file, line, call_type}]。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "caller": {
                    "type": "string",
                    "description": "按调用者的 qualified_name 过滤",
                },
                "callee": {
                    "type": "string",
                    "description": "按被调用者名称过滤（模糊匹配 callee_name 或 callee_resolved）",
                },
            },
            "required": [],
        }

    async def execute(self, *, caller: str | None = None, callee: str | None = None, **kwargs: Any) -> Any:
        conn = get_connection()
        try:
            query = (
                "SELECT caller, callee_name, callee_resolved, file, line, call_type "
                "FROM call_edges WHERE project_name = ?"
            )
            params: list[Any] = [self._project_name]

            if caller:
                query += " AND caller = ?"
                params.append(caller)
            if callee:
                query += " AND (callee_name = ? OR callee_resolved = ?)"
                params.extend([callee, callee])

            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "caller": r[0],
                    "callee_name": r[1],
                    "callee_resolved": r[2],
                    "file": r[3],
                    "line": r[4],
                    "call_type": r[5],
                }
                for r in rows
            ]
        finally:
            conn.close()
