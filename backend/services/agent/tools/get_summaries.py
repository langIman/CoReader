"""Agent Tool：获取项目摘要。"""

from typing import Any

from backend.dao.summary_store import get_summaries_by_type
from backend.services.agent.tools.base import BaseTool


class GetSummariesTool(BaseTool):
    """从 SQLite 获取指定类型的项目摘要。"""

    def __init__(self, project_name: str) -> None:
        self._project_name = project_name

    @property
    def name(self) -> str:
        return "get_summaries"

    @property
    def description(self) -> str:
        return (
            "获取项目的摘要列表。可按类型查询：file（文件摘要）、folder（文件夹摘要）、project（项目摘要）。"
            "返回 [{path, summary}] 格式的列表。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "summary_type": {
                    "type": "string",
                    "enum": ["file", "folder", "project"],
                    "description": "摘要类型：file=文件级, folder=文件夹级, project=项目级",
                },
            },
            "required": ["summary_type"],
        }

    async def execute(self, *, summary_type: str, **kwargs: Any) -> Any:
        return get_summaries_by_type(self._project_name, summary_type)
