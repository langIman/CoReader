"""QA 会话 / 消息持久化。

对应 QA_REFACTOR_PLAN.md §2.5：
- qa_conversations：一行一会话，按 (project_name, updated_at) 索引用于列表
- qa_messages：一行一消息，tool_events / code_refs 以 JSON 字段存
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from backend.dao.database import get_connection
from backend.models.qa_models import (
    Conversation,
    ConversationDetail,
    QAMessage,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------- 会话 ----------------------------


def create_conversation(project_name: str, title: str) -> str:
    """新建会话，返回 id。title 建议取首问题前 30 字符。"""
    conv_id = uuid.uuid4().hex
    now = _now_iso()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO qa_conversations (id, project_name, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (conv_id, project_name, title, now, now),
        )
        conn.commit()
        logger.info("QA conversation created: id=%s project=%s", conv_id, project_name)
        return conv_id
    finally:
        conn.close()


def list_conversations(project_name: str) -> list[Conversation]:
    """按 updated_at 倒序返回某项目下所有会话。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, project_name, title, created_at, updated_at "
            "FROM qa_conversations WHERE project_name = ? "
            "ORDER BY updated_at DESC",
            (project_name,),
        ).fetchall()
        return [
            Conversation(
                id=r["id"],
                project_name=r["project_name"],
                title=r["title"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]
    finally:
        conn.close()


def get_conversation(conversation_id: str) -> ConversationDetail | None:
    """读整个会话（元数据 + 消息按 id 升序）。不存在返回 None。"""
    conn = get_connection()
    try:
        conv_row = conn.execute(
            "SELECT id, project_name, title, created_at, updated_at "
            "FROM qa_conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if conv_row is None:
            return None

        msg_rows = conn.execute(
            "SELECT id, conversation_id, role, content, mode, "
            "tool_events_json, code_refs_json, stop_reason, tool_chain_json, created_at "
            "FROM qa_messages WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()

        messages = [_row_to_message(r) for r in msg_rows]

        return ConversationDetail(
            id=conv_row["id"],
            project_name=conv_row["project_name"],
            title=conv_row["title"],
            created_at=conv_row["created_at"],
            updated_at=conv_row["updated_at"],
            messages=messages,
        )
    finally:
        conn.close()


def delete_conversation(conversation_id: str) -> bool:
    """删除会话。返回是否命中了一行。消息通过 ON DELETE CASCADE 级联删。"""
    conn = get_connection()
    try:
        # SQLite 默认不开 foreign_keys，手动删消息再删会话保稳
        conn.execute(
            "DELETE FROM qa_messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        cur = conn.execute(
            "DELETE FROM qa_conversations WHERE id = ?",
            (conversation_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def touch_conversation(conversation_id: str) -> None:
    """刷新 updated_at。用于会话列表按最近活动排序。"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE qa_conversations SET updated_at = ? WHERE id = ?",
            (_now_iso(), conversation_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------- 消息 ----------------------------


def append_message(conversation_id: str, message: QAMessage) -> int:
    """追加一条消息，返回自增 id。message.id 会被忽略（由 DB 生成）。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO qa_messages "
            "(conversation_id, role, content, mode, tool_events_json, code_refs_json, "
            "stop_reason, tool_chain_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_id,
                message.role,
                message.content,
                message.mode,
                json.dumps(message.tool_events, ensure_ascii=False),
                json.dumps(message.code_refs, ensure_ascii=False),
                message.stop_reason,
                json.dumps(message.tool_chain, ensure_ascii=False) if message.tool_chain else None,
                message.created_at,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ---------------------------- 跨功能查询 ----------------------------


def get_user_questions(project_name: str) -> list[str]:
    """聚合某项目下所有会话里的 user 角色消息内容，按时间正序。

    Quiz 的 history 模式调用此函数收集"用户曾问过什么"作为出题素材。
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT m.content FROM qa_messages m "
            "JOIN qa_conversations c ON m.conversation_id = c.id "
            "WHERE c.project_name = ? AND m.role = 'user' "
            "ORDER BY m.id ASC",
            (project_name,),
        ).fetchall()
        return [r["content"] for r in rows]
    finally:
        conn.close()


# ---------------------------- 内部 ----------------------------


def _row_to_message(row) -> QAMessage:
    keys = row.keys() if hasattr(row, "keys") else []
    stop_reason = row["stop_reason"] if "stop_reason" in keys else None
    tool_chain_raw = row["tool_chain_json"] if "tool_chain_json" in keys else None
    return QAMessage(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=row["role"],
        content=row["content"],
        mode=row["mode"],
        tool_events=json.loads(row["tool_events_json"]) if row["tool_events_json"] else [],
        code_refs=json.loads(row["code_refs_json"]) if row["code_refs_json"] else {},
        stop_reason=stop_reason,
        tool_chain=json.loads(tool_chain_raw) if tool_chain_raw else [],
        created_at=row["created_at"],
    )
