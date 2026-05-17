"""Eval 共享工具：DB 连接、符号查询、文件行验证、报告输出。"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "backend" / "data" / "summaries.db"


def get_connection() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print(f"[ERROR] Database not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def load_symbols(project_name: str) -> set[str]:
    """Return all symbol names (unqualified) for a project."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT name FROM symbols WHERE project_name = ?",
            (project_name,),
        ).fetchall()
        return {r["name"] for r in rows}
    finally:
        conn.close()


def load_qualified_symbols(project_name: str) -> set[str]:
    """Return all qualified_name for a project."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT qualified_name FROM symbols WHERE project_name = ?",
            (project_name,),
        ).fetchall()
        return {r["qualified_name"] for r in rows}
    finally:
        conn.close()


def load_module_paths(project_name: str) -> set[str]:
    """Return all module file paths."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT path FROM modules WHERE project_name = ?",
            (project_name,),
        ).fetchall()
        return {r["path"] for r in rows}
    finally:
        conn.close()


def load_summaries_paths(project_name: str) -> set[str]:
    """Return all paths that have summaries."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT path FROM summaries WHERE project_name = ?",
            (project_name,),
        ).fetchall()
        return {r["path"] for r in rows}
    finally:
        conn.close()


def load_project_files(project_name: str) -> dict[str, str]:
    """Return {path: content} for all uploaded project files."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT path, content FROM project_files WHERE project_name = ?",
            (project_name,),
        ).fetchall()
        return {r["path"]: r["content"] for r in rows}
    finally:
        conn.close()


def check_file_lines(project_files: dict[str, str], file_path: str, start: int, end: int) -> bool:
    """Verify that a file exists in the project and the line range is valid."""
    content = project_files.get(file_path)
    if content is None:
        return False
    lines = content.split("\n")
    return 1 <= start <= end <= len(lines)


def extract_identifiers_strict(text: str) -> set[str]:
    """Extract only backticked identifiers — high confidence code references."""
    return set(re.findall(r"`([A-Za-z_]\w+)`", text))


def extract_identifiers(text: str) -> set[str]:
    """Extract potential code identifiers from text (backticked + CamelCase/snake_case with length filter)."""
    backticked = extract_identifiers_strict(text)
    camel_snake = set(re.findall(r"\b([A-Z][a-zA-Z0-9]{4,}|[a-z][a-z0-9]*_[a-z0-9_]{2,})\b", text))
    keywords = {"the", "and", "for", "not", "with", "this", "that", "from", "are", "was",
                "has", "have", "will", "can", "all", "its", "also", "into", "more", "some",
                "when", "than", "then", "each", "been", "them", "which", "their", "would",
                "could", "should", "about", "other", "these", "those", "over", "such",
                "only", "very", "just", "most", "both", "through", "between", "after",
                "before", "while", "where", "here", "there", "how", "why", "what",
                "true", "false", "none", "null", "return", "import", "class", "def",
                "self", "str", "int", "list", "dict", "bool", "float", "type", "None"}
    return backticked | (camel_snake - keywords)


def write_report(data: dict, output_path: Path) -> None:
    """Write JSON report and print one-line summary to stdout."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Report saved: {output_path}")


def report_path(prefix: str, project_name: str) -> Path:
    """Generate timestamped report path."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return PROJECT_ROOT / "eval" / "report" / f"{prefix}_{project_name}_{ts}.json"


def pct(num: int, den: int) -> float:
    """Safe percentage calculation."""
    if den == 0:
        return 0.0
    return round(num / den * 100, 1)
