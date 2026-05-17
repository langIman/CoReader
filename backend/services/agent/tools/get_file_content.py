"""Agent Tool：读取项目源文件内容（支持行范围 + 符号定位）。

对比 CC 的 FileReadTool(path, offset?, limit?)：
- 基础行范围：start_line / end_line（对齐 CC）
- 语义扩展：symbol_name 直接从 AST 查行号，LLM 无需记住行号（我们的优势）
"""

from typing import Any

from backend.dao.database import get_connection
from backend.dao.project_file_persist import load_project_files
from backend.services.agent.tools.base import BaseTool

# 单次读取最大行数：避免大文件一次性占满上下文
MAX_LINES_PER_READ = 300
# 读取符号时自动扩展的上下文行数（含 import / 装饰器等）
SYMBOL_CONTEXT_LINES = 3


class GetFileContentTool(BaseTool):
    """从 SQLite 读取指定文件的源码（进程重启后仍可用）。

    支持三种读取模式（优先级从高到低）：
    1. symbol_name → 从 AST 精确定位函数/类的行范围（我们的业务优势）
    2. start_line + end_line → 指定行范围（对齐 CC FileReadTool offset+limit）
    3. 两者都不传 → 读整个文件（超过 MAX_LINES_PER_READ 自动截断）
    """

    def __init__(self, project_name: str = "") -> None:
        self._project_name = project_name

    @property
    def name(self) -> str:
        return "get_file_content"

    @property
    def description(self) -> str:
        return (
            "读取项目中指定文件的源代码。支持三种模式：\n"
            "1. 传 symbol_name：直接读取该函数/类的代码（推荐，最省 token）\n"
            "2. 传 start_line + end_line：读取指定行范围\n"
            "3. 只传 path：读取整个文件（文件较大时建议先用 symbol_name 或行范围）"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件相对路径，如 'backend/services/file_service.py'",
                },
                "symbol_name": {
                    "type": "string",
                    "description": (
                        "函数或类的 qualified_name（来自 get_symbols / search_symbols），"
                        "如 'SeckillService.tryLock'。传此参数时自动定位行范围，无需手动指定。"
                    ),
                },
                "start_line": {
                    "type": "integer",
                    "description": "起始行号（1-indexed）",
                },
                "end_line": {
                    "type": "integer",
                    "description": "结束行号（1-indexed，含）",
                },
            },
            "required": ["path"],
        }

    async def execute(
        self,
        *,
        path: str,
        symbol_name: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        **kwargs: Any,
    ) -> Any:
        project_files = load_project_files(self._project_name)
        if not project_files:
            return {"error": "没有已加载的项目"}

        content = project_files.get(path)
        if content is None:
            available = sorted(project_files.keys())
            return {"error": f"文件不存在: {path}", "available_files": available}

        lines = content.splitlines()
        total_lines = len(lines)

        # 模式 1：symbol_name → 从 AST 查行范围
        if symbol_name:
            sym_range = self._lookup_symbol_lines(path, symbol_name)
            if sym_range is None:
                return {
                    "error": f"未找到符号: {symbol_name}",
                    "hint": "请用 get_symbols 或 search_symbols 确认正确的 qualified_name",
                }
            s, e = sym_range
            # 向前扩展 SYMBOL_CONTEXT_LINES 行（装饰器/注释/import）
            s = max(1, s - SYMBOL_CONTEXT_LINES)
            return self._slice(lines, s, e, total_lines, f"symbol:{symbol_name}")

        # 模式 2：行范围
        if start_line is not None or end_line is not None:
            s = max(1, start_line or 1)
            e = min(total_lines, end_line or total_lines)
            return self._slice(lines, s, e, total_lines, f"lines:{s}-{e}")

        # 模式 3：全文件（超长截断）
        if total_lines <= MAX_LINES_PER_READ:
            return {"path": path, "content": content, "total_lines": total_lines}

        # 超过上限：返回前 MAX_LINES_PER_READ 行 + 提示
        truncated = "\n".join(lines[:MAX_LINES_PER_READ])
        return {
            "path": path,
            "content": truncated,
            "total_lines": total_lines,
            "truncated": True,
            "shown_lines": f"1-{MAX_LINES_PER_READ}",
            "hint": f"文件共 {total_lines} 行，仅展示前 {MAX_LINES_PER_READ} 行。"
                    "请用 start_line/end_line 或 symbol_name 读取其他部分。",
        }

    def _lookup_symbol_lines(self, path: str, symbol_name: str) -> tuple[int, int] | None:
        """从 SQLite symbols 表查符号的行范围。"""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT line_start, line_end FROM symbols "
                "WHERE project_name = ? AND (qualified_name = ? OR name = ?) AND file = ? "
                "LIMIT 1",
                (self._project_name, symbol_name, symbol_name, path),
            ).fetchone()
            if row:
                return (row[0], row[1])
            # 宽松匹配：不限制 file（可能 qualified_name 已唯一）
            row = conn.execute(
                "SELECT line_start, line_end FROM symbols "
                "WHERE project_name = ? AND (qualified_name = ? OR name = ?) "
                "LIMIT 1",
                (self._project_name, symbol_name, symbol_name),
            ).fetchone()
            return (row[0], row[1]) if row else None
        finally:
            conn.close()

    @staticmethod
    def _slice(
        lines: list[str], start: int, end: int, total: int, label: str,
    ) -> dict:
        """截取 [start, end]（1-indexed，含两端）。"""
        end = min(end, start + MAX_LINES_PER_READ - 1)  # 单次读取上限
        chunk = "\n".join(lines[start - 1 : end])
        return {
            "content": chunk,
            "shown_lines": f"{start}-{end}",
            "total_lines": total,
            "mode": label,
        }
