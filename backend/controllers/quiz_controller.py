"""Quiz 测验 HTTP 控制器。

对应 QUIZ_FEATURE_PLAN.md §3.1。暴露 5 个端点：
- POST   /api/quiz/generate                       SSE 流式生成测验
- GET    /api/quiz/sessions                       项目下历史测验列表
- GET    /api/quiz/{session_id}                   session 详情（题目 + 已有答案）
- POST   /api/quiz/{session_id}/answer/{index}    提交单题答案
- DELETE /api/quiz/{session_id}                   删除测验

注意路由顺序：``/sessions`` 必须在 ``/{session_id}`` 之前注册，避免被 path 参数捕获。
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.models.quiz_models import (
    QuizAnswerRequest,
    QuizGenerateRequest,
    QuizSession,
    QuizSessionDetail,
)
from backend.services.quiz import quiz_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/quiz", tags=["quiz"])


# ---------------------------- 生成 ----------------------------


@router.post("/generate")
async def post_generate(req: QuizGenerateRequest):
    """生成测验。返回 SSE：thinking* → question×10 → done | error。"""

    async def sse_stream():
        try:
            async for event_name, payload in quiz_service.generate_quiz(req):
                data = json.dumps(payload, ensure_ascii=False)
                yield f"event: {event_name}\ndata: {data}\n\n"
        except Exception as e:
            logger.exception("Quiz generate failed")
            err = json.dumps({"message": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
            yield f"event: error\ndata: {err}\n\n"

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------- 列表 / 详情 / 删除 ----------------------------


@router.get("/sessions", response_model=list[QuizSession])
async def list_sessions(project_name: str) -> list[QuizSession]:
    """列出项目下所有测验，按创建时间倒序。"""
    return quiz_service.list_sessions(project_name)


@router.get("/{session_id}", response_model=QuizSessionDetail)
async def get_session(session_id: str) -> QuizSessionDetail:
    detail = quiz_service.get_session_detail(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Quiz session not found: {session_id}")
    return detail


@router.delete("/{session_id}")
async def delete_session(session_id: str) -> dict:
    ok = quiz_service.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Quiz session not found: {session_id}")
    return {"deleted": session_id}


# ---------------------------- 答题 ----------------------------


@router.post("/{session_id}/answer/{index}")
async def submit_answer(session_id: str, index: int, req: QuizAnswerRequest) -> dict:
    """提交某题的答案，返回 ``{is_correct, correct_key}``。"""
    result = await quiz_service.submit_answer(session_id, index, req.chosen_key)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Quiz session/question not found: {session_id}#{index}",
        )
    return result
