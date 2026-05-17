"""Quiz 测验持久化。

对应 QUIZ_FEATURE_PLAN.md §2.2。三张表：
- quiz_sessions：一行一会话（mode/source_id/score/answered_count/status）
- quiz_questions：一行一题（options/code_ref 以 JSON 存）
- quiz_answers：一行一作答（提交即写入，重复提交用 INSERT OR REPLACE）
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from backend.dao.database import get_connection
from backend.models.quiz_models import (
    QuizCodeRef,
    QuizMode,
    QuizOption,
    QuizQuestion,
    QuizSession,
    QuizSessionDetail,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------- 会话 ----------------------------


def create_session(
    project_name: str,
    mode: QuizMode,
    source_id: str | None,
    title: str,
) -> str:
    """新建测验会话，返回 id。status 初始为 'generating'。"""
    session_id = uuid.uuid4().hex
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO quiz_sessions "
            "(id, project_name, mode, source_id, title, status, score, answered_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'generating', 0, 0, ?)",
            (session_id, project_name, mode.value, source_id, title, _now_iso()),
        )
        conn.commit()
        logger.info("Quiz session created: id=%s mode=%s project=%s", session_id, mode.value, project_name)
        return session_id
    finally:
        conn.close()


def update_session_status(session_id: str, status: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE quiz_sessions SET status = ? WHERE id = ?",
            (status, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_sessions(project_name: str) -> list[QuizSession]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, project_name, mode, source_id, title, status, score, answered_count, created_at "
            "FROM quiz_sessions WHERE project_name = ? ORDER BY created_at DESC",
            (project_name,),
        ).fetchall()
        return [_row_to_session(r) for r in rows]
    finally:
        conn.close()


def get_session_detail(session_id: str) -> QuizSessionDetail | None:
    """读会话 + 所有题目 + 已提交答案。"""
    conn = get_connection()
    try:
        s_row = conn.execute(
            "SELECT id, project_name, mode, source_id, title, status, score, answered_count, created_at "
            "FROM quiz_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if s_row is None:
            return None

        q_rows = conn.execute(
            "SELECT id, session_id, idx, question_text, options_json, correct_key, code_ref_json "
            "FROM quiz_questions WHERE session_id = ? ORDER BY idx ASC",
            (session_id,),
        ).fetchall()
        questions = [_row_to_question(r) for r in q_rows]

        a_rows = conn.execute(
            "SELECT question_index, chosen_key FROM quiz_answers WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        answers = {r["question_index"]: r["chosen_key"] for r in a_rows}

        session = _row_to_session(s_row)
        return QuizSessionDetail(
            **session.model_dump(),
            questions=questions,
            answers=answers,
        )
    finally:
        conn.close()


def delete_session(session_id: str) -> bool:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM quiz_answers WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM quiz_questions WHERE session_id = ?", (session_id,))
        cur = conn.execute("DELETE FROM quiz_sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------- 题目 ----------------------------


def save_question(session_id: str, index: int, question: QuizQuestion) -> str:
    """保存单题，返回 question id。会议生成的 id 写回 question 对象。"""
    q_id = uuid.uuid4().hex
    options_json = json.dumps(
        [o.model_dump() for o in question.options],
        ensure_ascii=False,
    )
    code_ref_json = (
        json.dumps(question.code_ref.model_dump(), ensure_ascii=False)
        if question.code_ref is not None
        else None
    )
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO quiz_questions "
            "(id, session_id, idx, question_text, options_json, correct_key, code_ref_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (q_id, session_id, index, question.question_text, options_json,
             question.correct_key, code_ref_json),
        )
        conn.commit()
        question.id = q_id
        question.session_id = session_id
        return q_id
    finally:
        conn.close()


# ---------------------------- 作答 ----------------------------


def save_answer(
    session_id: str,
    question_index: int,
    chosen_key: str,
    is_correct: bool,
) -> None:
    """保存作答（重复提交用 REPLACE，并相应回滚旧的 score/answered_count）。"""
    conn = get_connection()
    try:
        # 先看是否已答过同一题，避免重复递增计数
        old = conn.execute(
            "SELECT is_correct FROM quiz_answers WHERE session_id = ? AND question_index = ?",
            (session_id, question_index),
        ).fetchone()

        conn.execute(
            "INSERT OR REPLACE INTO quiz_answers "
            "(session_id, question_index, chosen_key, is_correct, answered_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, question_index, chosen_key, int(is_correct), _now_iso()),
        )

        if old is None:
            # 新作答：递增 answered_count，正确再 +1 score
            if is_correct:
                conn.execute(
                    "UPDATE quiz_sessions SET answered_count = answered_count + 1, "
                    "score = score + 1 WHERE id = ?",
                    (session_id,),
                )
            else:
                conn.execute(
                    "UPDATE quiz_sessions SET answered_count = answered_count + 1 WHERE id = ?",
                    (session_id,),
                )
        else:
            # 修改作答：answered_count 不变，score 按差值调整
            old_correct = bool(old["is_correct"])
            if old_correct and not is_correct:
                conn.execute(
                    "UPDATE quiz_sessions SET score = score - 1 WHERE id = ?",
                    (session_id,),
                )
            elif not old_correct and is_correct:
                conn.execute(
                    "UPDATE quiz_sessions SET score = score + 1 WHERE id = ?",
                    (session_id,),
                )

        conn.commit()
    finally:
        conn.close()


# ---------------------------- 内部 ----------------------------


def _row_to_session(row) -> QuizSession:
    return QuizSession(
        id=row["id"],
        project_name=row["project_name"],
        mode=QuizMode(row["mode"]),
        source_id=row["source_id"],
        title=row["title"],
        status=row["status"],
        score=row["score"],
        answered_count=row["answered_count"],
        created_at=row["created_at"],
    )


def _row_to_question(row) -> QuizQuestion:
    options_raw = json.loads(row["options_json"])
    options = [QuizOption(**o) for o in options_raw]
    code_ref = None
    if row["code_ref_json"]:
        try:
            code_ref = QuizCodeRef(**json.loads(row["code_ref_json"]))
        except Exception:
            logger.warning("Invalid code_ref_json for question %s", row["id"])
    return QuizQuestion(
        id=row["id"],
        session_id=row["session_id"],
        index=row["idx"],
        question_text=row["question_text"],
        options=options,
        correct_key=row["correct_key"],
        code_ref=code_ref,
    )
