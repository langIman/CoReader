"""QA 问答功能数据模型。

对应 QA_REFACTOR_PLAN.md §2.3：两种模式共用的请求/响应/持久化模型。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


QAMode = Literal["fast", "deep"]


class QARequest(BaseModel):
    """POST /api/qa/ask 请求体。"""

    project_name: str
    conversation_id: str | None = None   # None → 新建
    question: str
    mode: QAMode = "fast"


class QAMessage(BaseModel):
    """单条消息（user 或 assistant）。"""

    id: int = 0                          # append 后由 DAO 回填
    conversation_id: str
    role: Literal["user", "assistant"]
    content: str
    mode: QAMode | None = None           # 仅 assistant 才有
    tool_events: list[dict[str, Any]] = Field(default_factory=list)  # deep 模式记录
    code_refs: dict[str, dict] = Field(default_factory=dict)         # ref_id → {file, start_line, end_line, symbol?}
    stop_reason: str | None = None       # assistant 才有：completed / max_iterations / cancelled / model_error / compact_failed
    tool_chain: list[dict] = Field(default_factory=list)  # deep 模式完整工具链（OpenAI 格式 messages），供下轮注入
    created_at: str


class Conversation(BaseModel):
    """会话元数据（不含消息）。"""

    id: str
    project_name: str
    title: str                           # 默认取首问题前 30 字符
    created_at: str
    updated_at: str


class ConversationDetail(Conversation):
    """会话详情（含所有消息）。"""

    messages: list[QAMessage] = Field(default_factory=list)
