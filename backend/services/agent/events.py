"""Agent 主循环的事件流类型。

run_stream() 按时间顺序 yield 这些事件给消费者（QA、Wiki 等）。
discriminated union 走 Pydantic v2 + Literal type 字段。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

StopReason = Literal[
    "completed",        # 模型给出最终文本
    "max_iterations",   # 撞工具循环上限
    "cancelled",        # 消费者通过 cancel_event 取消
    "model_error",      # LLM 调用异常（阶段 5 才会 yield；阶段 1 仍直接 raise）
    "compact_failed",   # autocompact 失败（阶段 4 引入）
]


class TextDelta(BaseModel):
    """文本增量。阶段 1 不 yield；保留给阶段 6 流式分片使用。"""
    type: Literal["text_delta"] = "text_delta"
    delta: str


class ToolUseStart(BaseModel):
    """工具调用 dispatch 时立即 yield，用于前端展示"正在调用 X"。"""
    type: Literal["tool_use_start"] = "tool_use_start"
    iteration: int
    tool_id: str
    name: str
    args: dict[str, Any]


class ToolResult(BaseModel):
    """单个工具完成后 yield。preview 给前端、full 给消费者持久化。"""
    type: Literal["tool_result"] = "tool_result"
    iteration: int
    tool_id: str
    name: str
    ok: bool
    preview: str
    full: str


class IterationEnd(BaseModel):
    """一轮 LLM + 工具完成。input/output_tokens 阶段 1 暂填 0，阶段 4 接 token 估算时回填。"""
    type: Literal["iteration_end"] = "iteration_end"
    iteration: int
    input_tokens: int = 0
    output_tokens: int = 0


class CompactBoundary(BaseModel):
    """autocompact 边界事件。阶段 4 才会 yield。"""
    type: Literal["compact_boundary"] = "compact_boundary"
    summarized_turns: int
    new_input_tokens: int = 0


class Stop(BaseModel):
    """主循环出口。final_text 是给 run() 的便捷返回值。"""
    type: Literal["stop"] = "stop"
    reason: StopReason
    final_text: str


AgentEvent = Annotated[
    Union[TextDelta, ToolUseStart, ToolResult, IterationEnd, CompactBoundary, Stop],
    Field(discriminator="type"),
]
