"""Quiz 编排服务：消费 Agent 事件流 → 翻译为 SSE 帧。

对应 QUIZ_FEATURE_PLAN.md §4.2 + §3.2。两阶段：
- 阶段一：Agent 工具调用 → ``thinking`` 事件
- 阶段二：Stop 后解析 JSON → 逐题 ``question`` 事件（100ms 间隔，动画感）

公共入口 ``generate_quiz(req)``：async generator yield ``(event_name, payload)``，
controller 序列化为 SSE 帧。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from backend.dao import qa_store, quiz_store
from backend.dao.wiki_store import load_page
from backend.models.quiz_models import (
    QuizGenerateRequest,
    QuizMode,
    QuizSession,
    QuizSessionDetail,
)
from backend.services.quiz import quiz_generator

logger = logging.getLogger(__name__)


# 工具名 → 用户可读的状态文本（QUIZ_FEATURE_PLAN.md §七关键约束）
_TOOL_STATUS: dict[str, str] = {
    "get_summaries": "正在读取项目摘要...",
    "get_modules": "正在扫描模块结构...",
    "get_symbols": "正在收集符号...",
    "get_call_edges": "正在分析调用关系...",
    "get_file_content": "正在读取代码...",
    "search_symbols": "正在搜索符号...",
    "search_code": "正在搜索代码...",
}


# 逐题推送间隔（动画感），不可设过大，避免长尾占用连接
_QUESTION_PUSH_DELAY = 0.1


def _make_title(req: QuizGenerateRequest, page_title: str | None = None) -> str:
    if req.mode == QuizMode.history:
        return f"{req.project_name} · 问答历史回顾测验"
    if req.mode == QuizMode.page:
        return f"{page_title or req.source_id} · 页面专项测验"
    return f"{req.project_name} · 全项目随机测验"


# ---------------------------- 出题流（SSE 源头）----------------------------


async def generate_quiz(
    req: QuizGenerateRequest,
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """编排出题流程：预加载 → 创建 session → 消费 Agent → 推送题目。"""

    # 1. 模式预加载
    qa_questions: list[str] | None = None
    page_title: str | None = None
    page_content: str | None = None

    if req.mode == QuizMode.history:
        qa_questions = qa_store.get_user_questions(req.project_name)
        if not qa_questions:
            yield ("error", {"message": "该项目暂无 QA 对话历史，请先在问答区提问后再使用历史模式。"})
            return

    elif req.mode == QuizMode.page:
        if not req.source_id:
            yield ("error", {"message": "page 模式需要提供 source_id（wiki 页面 id）。"})
            return
        page = load_page(req.project_name, req.source_id)
        if not page or not page.content_md:
            yield ("error", {"message": f"Wiki 页面不存在或内容为空：{req.source_id}"})
            return
        page_title = page.title
        page_content = page.content_md

    title = _make_title(req, page_title)

    # 2. 创建 session
    session_id = quiz_store.create_session(req.project_name, req.mode, req.source_id, title)

    # 3. 消费 Agent 事件流
    try:
        async for ev in quiz_generator.generate(
            req,
            qa_questions=qa_questions,
            page_title=page_title,
            page_content=page_content,
        ):
            if ev.type == "tool_use_start":
                status = _TOOL_STATUS.get(ev.name, f"正在调用 {ev.name}...")
                yield ("thinking", {"tool": ev.name, "status": status})

            elif ev.type == "stop":
                if ev.reason != "completed":
                    quiz_store.update_session_status(session_id, "done")
                    yield ("error", {"message": f"生成失败：{ev.reason}"})
                    return

                # 4. 解析 + 逐题推送
                try:
                    questions = quiz_generator.parse_questions(ev.final_text)
                except ValueError as e:
                    quiz_store.update_session_status(session_id, "done")
                    logger.exception("Quiz JSON parse failed")
                    yield ("error", {"message": f"题目解析失败：{e}"})
                    return

                for i, q in enumerate(questions):
                    quiz_store.save_question(session_id, i, q)
                    yield ("question", {
                        "index": i,
                        "question_text": q.question_text,
                        "options": [o.model_dump() for o in q.options],
                        "correct_key": q.correct_key,
                        "code_ref": q.code_ref.model_dump() if q.code_ref else None,
                    })
                    await asyncio.sleep(_QUESTION_PUSH_DELAY)

                quiz_store.update_session_status(session_id, "ready")
                yield ("done", {"session_id": session_id, "title": title})
                return

            # text_delta / tool_result / iteration_end / compact_boundary 暂不透传

    except Exception as e:
        logger.exception("Quiz generation failed")
        quiz_store.update_session_status(session_id, "done")
        yield ("error", {"message": f"{type(e).__name__}: {e}"})


# ---------------------------- 答题 ----------------------------


async def submit_answer(
    session_id: str,
    question_index: int,
    chosen_key: str,
) -> dict[str, Any] | None:
    """保存作答，返回 ``{is_correct, correct_key}``。session/question 不存在返回 None。"""
    detail = quiz_store.get_session_detail(session_id)
    if detail is None:
        return None

    q_map = {q.index: q for q in detail.questions}
    q = q_map.get(question_index)
    if q is None:
        return None

    is_correct = chosen_key.upper().strip() == q.correct_key.upper().strip()
    quiz_store.save_answer(session_id, question_index, chosen_key, is_correct)

    # 全部答完则标记 done
    new_answered = max(detail.answered_count, 0) + (
        0 if question_index in detail.answers else 1
    )
    if new_answered >= len(detail.questions):
        quiz_store.update_session_status(session_id, "done")

    return {"is_correct": is_correct, "correct_key": q.correct_key}


# ---------------------------- 查询封装 ----------------------------


def list_sessions(project_name: str) -> list[QuizSession]:
    return quiz_store.list_sessions(project_name)


def get_session_detail(session_id: str) -> QuizSessionDetail | None:
    return quiz_store.get_session_detail(session_id)


def delete_session(session_id: str) -> bool:
    return quiz_store.delete_session(session_id)
