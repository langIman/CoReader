"""QA 问答编排服务。

公共入口 ``answer(req)``：async generator，yield ``(event_name, payload)``；
controller 把这些序列化成 SSE 帧。内部哨兵 ``__final__`` 不转发，仅用来收
``content / tool_events / stop_reason``。

阶段 2 重构（QA_AGENT_LOOP_REFACTOR_PLAN.md）：
- ``_deep_stream`` 已删除；deep 模式改成消费 ``Agent.run_stream()``
- ``_fast_stream`` 不变（fast 是单次 LLM 流式调用，不需要 Agent 主循环）
- ``DEEP_TOKEN_BUDGET`` / ``budget_exhausted`` 路径已移除（向 CC 看齐：靠
  Agent.max_iterations 兜底，长会话压缩在阶段 4 由 Compactor 接管）
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from backend.dao.qa_store import (
    append_message,
    create_conversation,
    get_conversation,
    touch_conversation,
)
from backend.models.qa_models import QAMessage, QARequest
from backend.services.agent.agent import Agent
from backend.services.agent.compactor import LLMCompactor
from backend.services.agent.tools.get_call_edges import GetCallEdgesTool
from backend.services.agent.tools.get_file_content import GetFileContentTool
from backend.services.agent.tools.get_modules import GetModulesTool
from backend.services.agent.tools.get_summaries import GetSummariesTool
from backend.services.agent.tools.get_symbols import GetSymbolsTool
from backend.services.agent.tools.search_code import SearchCodeTool
from backend.services.agent.tools.search_symbols import SearchSymbolsTool
from backend.services.llm.llm_service import stream_messages
from backend.services.qa.code_refs import parse_code_refs
from backend.services.qa.context_builder import MAX_ITER_DEEP, QAContextBuilder

logger = logging.getLogger(__name__)

# 伪流式分片参数（用于把 Agent 一次性吐出的 final_text 切片成 token 流）
PSEUDO_STREAM_CHUNK_SIZE = 20
PSEUDO_STREAM_DELAY = 0.02

# autocompact 触发阈值：撞此 token 数后摘要旧消息（替换原 budget_exhausted 关 tools）
DEEP_TOKEN_BUDGET = 20_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------- 公共入口 ----------------------------


async def answer(req: QARequest) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """编排入口：持久化 user 消息 → 委派模式流 → 持久化 assistant 消息。

    yield ``(event_name, payload)``；controller 把这些序列化成 SSE 帧。
    内部哨兵 ``__final__`` 不转发，仅用来收 content / tool_events。
    """
    # 1. 会话 id（新建或复用）
    conv_id = req.conversation_id or create_conversation(
        req.project_name, req.question[:30],
    )

    # 2. 持久化 user 消息
    user_msg_id = append_message(conv_id, QAMessage(
        conversation_id=conv_id,
        role="user",
        content=req.question,
        created_at=_now_iso(),
    ))

    # 3. 发 start
    yield ("start", {
        "conversation_id": conv_id,
        "user_message_id": user_msg_id,
        "mode": req.mode,
    })

    # 4. 委派流式事件；同时收集最终 content / tool_events / stop_reason
    collected: list[str] = []
    tool_events: list[dict] = []
    stop_reason: str = "completed"
    tool_chain: list[dict] = []

    inner = _fast_stream(req) if req.mode == "fast" else _deep_stream(req)
    try:
        async for event in inner:
            name, payload = event
            if name == "__final__":
                collected.append(payload.get("content", ""))
                tool_events = payload.get("tool_events", [])
                stop_reason = payload.get("stop_reason", "completed")
                tool_chain = payload.get("tool_chain", [])
                continue
            yield event
    except Exception as e:
        logger.exception("QA inner stream failed")
        yield ("error", {"message": f"{type(e).__name__}: {e}"})
        return

    # 5. 解析 code_refs 块
    full_content = "".join(collected)
    clean_content, code_refs = parse_code_refs(full_content)

    # 6. 持久化 assistant 消息
    assistant_msg_id = append_message(conv_id, QAMessage(
        conversation_id=conv_id,
        role="assistant",
        content=clean_content,
        mode=req.mode,
        tool_events=tool_events,
        code_refs=code_refs,
        stop_reason=stop_reason,
        tool_chain=tool_chain,
        created_at=_now_iso(),
    ))
    touch_conversation(conv_id)

    # 7. 发 code_refs + done
    # done 带上净化后的 content，供前端把 bubble 里残留的 code_refs 块替换掉
    yield ("code_refs", {"refs": code_refs})
    yield ("done", {
        "assistant_message_id": assistant_msg_id,
        "content": clean_content,
        "stop_reason": stop_reason,
    })


# ---------------------------- 快速模式 ----------------------------


async def _fast_stream(
    req: QARequest,
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """快速模式：BM25 预检索 → 装配 Context → stream_messages → token*。"""
    ctx = QAContextBuilder(req.project_name, req.question, "fast").build()
    buf: list[str] = []
    async for chunk in stream_messages(ctx.to_messages()):
        buf.append(chunk)
        yield ("token", {"delta": chunk})
    yield ("__final__", {"content": "".join(buf), "tool_events": []})


# ---------------------------- 深度模式 ----------------------------


async def _pseudo_stream(
    content: str,
    chunk_size: int = PSEUDO_STREAM_CHUNK_SIZE,
    delay: float = PSEUDO_STREAM_DELAY,
) -> AsyncGenerator[str, None]:
    """把非流式最终答复切片模拟打字效果。"""
    for i in range(0, len(content), chunk_size):
        yield content[i : i + chunk_size]
        await asyncio.sleep(delay)


def _inject_history(agent: Agent, conversation_id: str) -> None:
    """从 DB 加载会话历史，转换后委托 agent.inject_history 注入 Context。

    职责：从 DB 读数据、配对 user+assistant 消息、构造 turns 结构。
    上下文恢复逻辑（restore_turn）已下沉到 Context 层，此处不再包含任何
    Context 操作——完全解耦（QA 层只管"加载什么"，Context 层管"怎么恢复"）。
    """
    detail = get_conversation(conversation_id)
    if not detail or len(detail.messages) <= 1:
        return

    # messages 按 id ASC，去掉最后一条（当前轮的 user，由 run_stream 自己 add_user）
    history = detail.messages[:-1]

    # 把消息配对：每个 user 对应后面一条 assistant
    turns: list[tuple[str, list[dict], str]] = []
    i = 0
    while i < len(history):
        msg = history[i]
        if msg.role == "user":
            user_content = msg.content
            tool_chain: list[dict] = []
            fallback_assistant = ""
            if i + 1 < len(history) and history[i + 1].role == "assistant":
                asst = history[i + 1]
                tool_chain = asst.tool_chain or []
                fallback_assistant = asst.content if not tool_chain else ""
                i += 2
            else:
                i += 1
            turns.append((user_content, tool_chain, fallback_assistant))
        else:
            i += 1  # 跳过孤立的 assistant（理论上不会发生）

    agent.inject_history(turns)
    logger.info(
        "QA deep: injected %d turns from conv=%s into Agent Context",
        len(turns), conversation_id,
    )


def _build_deep_tools(project_name: str) -> list:
    """深度模式工具集（QA 自有的检索 / 分析 7 件套）。

    SQLite 查询工具在构造时注入 project_name，避免依赖进程内存全局变量
    （内存在服务重启后清零会导致 "没有已加载的项目" 错误）。
    SearchCodeTool / GetFileContentTool 依赖内存文件内容，暂保持原样。
    """
    return [
        GetSummariesTool(project_name),
        GetModulesTool(project_name),
        GetSymbolsTool(project_name),
        GetCallEdgesTool(project_name),
        GetFileContentTool(project_name),
        SearchSymbolsTool(project_name),
        SearchCodeTool(project_name),
    ]


async def _deep_stream(
    req: QARequest,
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """深度模式：消费 ``Agent.run_stream()`` 事件流 + 翻译为 SSE 帧。

    主循环已搬到 [agent.py](../agent/agent.py)。本函数只做：
    1. 装配 system_prompt + first_user
    2. 实例化 Agent（关闭 SpawnAgentTool 自动注入，QA 不需要子 agent）
    3. 翻译 AgentEvent → SSE 帧
    4. Stop 时把 final_text 切片伪流式吐 token，再 yield ``__final__``
    """
    builder = QAContextBuilder(req.project_name, req.question, "deep")
    system_prompt, first_user = builder.build_for_agent()

    agent = Agent(
        system_prompt=system_prompt,
        tools=_build_deep_tools(req.project_name),
        max_iterations=MAX_ITER_DEEP,
        auto_spawn_agent=False,
        token_budget=DEEP_TOKEN_BUDGET,
        compactor=LLMCompactor(),
    )

    # 注入会话历史：每次 ask 新建 Agent，历史必须从 DB 读回来喂给 LLM
    # 排除最新一条（当前轮的 user 消息），它作为 first_user 传给 run_stream
    if req.conversation_id:
        _inject_history(agent, req.conversation_id)

    tool_events: list[dict] = []
    pending_args: dict[str, dict] = {}  # tool_id -> args，用于 ToolUseStart/Result 配对

    async for ev in agent.run_stream(first_user):
        if ev.type == "tool_use_start":
            pending_args[ev.tool_id] = ev.args
            yield ("tool_call", {
                "iteration": ev.iteration,
                "name": ev.name,
                "args_preview": ev.args,
            })

        elif ev.type == "tool_result":
            args = pending_args.pop(ev.tool_id, {})
            yield ("tool_result", {
                "iteration": ev.iteration,
                "name": ev.name,
                "ok": ev.ok,
                "preview": ev.preview,
            })
            tool_events.append({
                "iteration": ev.iteration,
                "name": ev.name,
                "args": args,
                "result_preview": ev.preview,
            })

        elif ev.type == "iteration_end":
            # 阶段 6 才在前端渲染轮次分隔；当前不发
            continue

        elif ev.type == "compact_boundary":
            yield ("compact_boundary", {
                "summarized_turns": ev.summarized_turns,
                "new_input_tokens": ev.new_input_tokens,
            })

        elif ev.type == "stop":
            # 切片伪流式吐 token（先剥掉 code_refs 块以免裸 JSON 流给前端）
            raw_content = ev.final_text
            clean_content, _ = parse_code_refs(raw_content)
            async for chunk in _pseudo_stream(clean_content):
                yield ("token", {"delta": chunk})
            # 收集完整工具链（_messages[1:] = 去掉首条 user 消息后的全部链路）
            # 下轮注入时 LLM 能看到本轮调了哪些工具、工具返回了什么原始数据（CC 对齐）
            tool_chain = agent._context._messages[1:]
            yield ("__final__", {
                "content": raw_content,
                "tool_events": tool_events,
                "stop_reason": ev.reason,
                "tool_chain": tool_chain,
            })
            return
