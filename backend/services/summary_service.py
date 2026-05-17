import ast
import asyncio
import logging
from collections import defaultdict

from backend.config import QWEN_FAST_MODEL, SUMMARY_TRUNCATION_PERCENT, SUMMARY_FUNC_LINES
from backend.dao.file_store import get_project_files, get_project_name
from backend.dao.summary_store import save_summary, clear_project_summaries
from backend.services.llm.llm_service import call_qwen
from backend.services.llm.prompts.summary_prompts import (
    build_file_summary_prompt,
    build_folder_summary_prompt,
    build_project_summary_prompt,
)
from backend.services.progress_reporter import get_reporter
from backend.config import MAX_WORKER_CONCURRENCY

logger = logging.getLogger(__name__)

INSUFFICIENT_INFO = "信息不足无法推测"


def extract_file_skeleton(content: str, file_path: str = "") -> str:
    """用 AST 提取每个函数前N行、每个类前N行，受截断百分比上限限制。

    根据文件语言自动选择解析器：Python 用内置 ast，其他语言走 tree-sitter。
    """
    from backend.config import get_file_language
    from backend.utils.analysis.ts_parser import get_lang_config, ts_extract_skeleton

    # 非 Python 文件走 tree-sitter
    lang = get_file_language(file_path) if file_path else None
    if lang and lang != "python":
        config = get_lang_config(lang)
        if config:
            return ts_extract_skeleton(content, config)

    # Python 走原有 ast 管道
    lines = content.split("\n")
    total_lines = len(lines)
    max_extract_lines = max(int(total_lines * SUMMARY_TRUNCATION_PERCENT), 10)

    try:
        tree = ast.parse(content)
    except SyntaxError:
        # AST 解析失败，退回截取前 max_extract_lines 行
        return "\n".join(lines[:max_extract_lines])

    extracted_ranges: list[tuple[int, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno - 1  # 0-indexed
            end = min(start + SUMMARY_FUNC_LINES, total_lines)
            extracted_ranges.append((start, end))

    if not extracted_ranges:
        return "\n".join(lines[:max_extract_lines])

    # 按起始行排序并去重
    extracted_ranges.sort()
    merged: list[tuple[int, int]] = []
    for start, end in extracted_ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # 收集行，受截断上限限制
    result_lines: list[str] = []
    for start, end in merged:
        for i in range(start, end):
            result_lines.append(lines[i])
            if len(result_lines) >= max_extract_lines:
                return "\n".join(result_lines)
        result_lines.append("...")  # 省略标记

    return "\n".join(result_lines)


def group_files_by_folder(project_files: dict[str, str]) -> dict[str, list[str]]:
    """按直接父文件夹分组文件路径。"""
    groups: dict[str, list[str]] = defaultdict(list)
    for path in project_files:
        parts = path.rsplit("/", 1)
        folder = parts[0] if len(parts) > 1 else "."
        groups[folder].append(path)
    return dict(groups)


def build_folder_tree(folders: set[str]) -> list[list[str]]:
    """构建自底向上的处理层级。返回从最深到最浅的文件夹列表。"""
    if not folders:
        return []

    # 按深度分层
    depth_map: dict[int, list[str]] = defaultdict(list)
    for folder in folders:
        depth = folder.count("/")
        depth_map[depth].append(folder)

    # 从最深到最浅
    max_depth = max(depth_map.keys())
    levels = []
    for d in range(max_depth, -1, -1):
        if d in depth_map:
            levels.append(depth_map[d])

    return levels


async def _generate_single_file_summary(
    file_path: str, content: str, project_name: str, semaphore: asyncio.Semaphore
) -> tuple[str, str]:
    """生成单个文件的摘要，含重传机制。"""
    async with semaphore:
        async with get_reporter().track("file_summary", file_path):
            # 第一次：用骨架
            skeleton = extract_file_skeleton(content, file_path=file_path)
            system, user = build_file_summary_prompt(file_path, skeleton)
            summary = await call_qwen(system, user, enable_thinking=False, model=QWEN_FAST_MODEL)

            if INSUFFICIENT_INFO in summary:
                # 重传：用完整内容
                logger.info("File %s: retrying with full content", file_path)
                system, user = build_file_summary_prompt(file_path, content)
                summary = await call_qwen(system, user, enable_thinking=False, model=QWEN_FAST_MODEL)

                if INSUFFICIENT_INFO in summary:
                    summary = "该文件/LLM出错"

            save_summary(file_path, "file", summary, project_name)
            logger.info("File summary done: %s", file_path)
            return file_path, summary


async def _generate_folder_summary(
    folder_path: str,
    child_summaries: list[tuple[str, str]],
    project_name: str,
) -> str:
    """生成文件夹摘要，含重传机制。"""
    async with get_reporter().track("folder_summary", folder_path):
        system, user = build_folder_summary_prompt(folder_path, child_summaries)
        summary = await call_qwen(system, user, enable_thinking=False, model=QWEN_FAST_MODEL)

        if INSUFFICIENT_INFO in summary:
            # 重传
            logger.info("Folder %s: retrying", folder_path)
            summary = await call_qwen(system, user, enable_thinking=False, model=QWEN_FAST_MODEL)
            if INSUFFICIENT_INFO in summary:
                summary = "该文件夹/LLM出错"

        save_summary(folder_path, "folder", summary, project_name)
        logger.info("Folder summary done: %s", folder_path)
        return summary


async def generate_hierarchical_summary() -> dict:
    """自底向上生成：文件摘要 → 文件夹摘要 → 项目摘要。"""
    project_files = get_project_files()
    project_name = get_project_name()

    if not project_files or not project_name:
        raise ValueError("No project loaded")

    # 清除旧摘要
    clear_project_summaries(project_name)

    semaphore = asyncio.Semaphore(MAX_WORKER_CONCURRENCY)

    # === 第一层：文件摘要（并发）===
    file_tasks = [
        _generate_single_file_summary(path, content, project_name, semaphore)
        for path, content in project_files.items()
    ]
    file_results = await asyncio.gather(*file_tasks)
    file_summary_map = dict(file_results)  # path -> summary

    # === 第二层：文件夹摘要（自底向上）===
    folder_groups = group_files_by_folder(project_files)
    all_folders = set(folder_groups.keys())

    # 添加中间文件夹（如 backend/services 有子文件夹 backend/services/agents）
    for folder in list(all_folders):
        parts = folder.split("/")
        for i in range(1, len(parts)):
            all_folders.add("/".join(parts[:i]))

    levels = build_folder_tree(all_folders)
    folder_summary_map: dict[str, str] = {}  # folder -> summary

    for level in levels:
        level_tasks = []
        for folder in level:
            # 收集该文件夹的直接子项摘要
            child_summaries: list[tuple[str, str]] = []

            # 直接子文件
            if folder in folder_groups:
                for file_path in folder_groups[folder]:
                    name = file_path.rsplit("/", 1)[-1]
                    child_summaries.append((name, file_summary_map.get(file_path, "")))

            # 直接子文件夹
            for other_folder, summary in folder_summary_map.items():
                parent = other_folder.rsplit("/", 1)[0] if "/" in other_folder else "."
                if parent == folder:
                    child_name = other_folder.rsplit("/", 1)[-1]
                    child_summaries.append((child_name + "/", summary))

            if child_summaries:
                level_tasks.append((folder, child_summaries))

        # 同层文件夹并发处理
        async def _process_folder(f: str, cs: list[tuple[str, str]]) -> tuple[str, str]:
            s = await _generate_folder_summary(f, cs, project_name)
            return f, s

        results = await asyncio.gather(
            *[_process_folder(f, cs) for f, cs in level_tasks]
        )
        for folder, summary in results:
            folder_summary_map[folder] = summary

    # === 第三层：项目摘要 ===
    # 收集顶层文件夹和根目录文件
    top_summaries: list[tuple[str, str]] = []

    for folder, summary in folder_summary_map.items():
        if "/" not in folder and folder != ".":
            top_summaries.append((folder + "/", summary))

    # 根目录文件
    if "." in folder_groups:
        for file_path in folder_groups["."]:
            name = file_path.rsplit("/", 1)[-1]
            top_summaries.append((name, file_summary_map.get(file_path, "")))

    async with get_reporter().track("project_summary", None):
        system, user = build_project_summary_prompt(project_name, top_summaries)
        project_summary = await call_qwen(system, user, enable_thinking=False, model=QWEN_FAST_MODEL)

        if INSUFFICIENT_INFO in project_summary:
            project_summary = await call_qwen(system, user, enable_thinking=False, model=QWEN_FAST_MODEL)
            if INSUFFICIENT_INFO in project_summary:
                project_summary = "该项目/LLM出错"

        save_summary(project_name, "project", project_summary, project_name)

    return {
        "project_name": project_name,
        "project_summary": project_summary,
    }
