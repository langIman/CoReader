"""通用 Agent -- 基于主循环的 LLM Agent 框架。

核心循环: 用户输入 → 上下文组装 → 模型决策 → 工具执行 → 结果注入 → 继续/停止

阶段 1（QA_AGENT_LOOP_REFACTOR_PLAN.md）落地：
- 加 ``run_stream() -> AsyncGenerator[AgentEvent]`` 公共接口，QA 等消费者走这个
- ``run()`` 重写为消费 ``run_stream`` 取最终文本（保持字节级等价行为）
- ``__init__`` 签名零变更（module_service / SpawnAgentTool 不受影响）
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from backend.services.agent.compactor import Compactor
from backend.services.agent.context.base import Context
from backend.services.agent.events import (
    AgentEvent,
    CompactBoundary,
    IterationEnd,
    Stop,
    ToolResult,
    ToolUseStart,
)
from backend.services.agent.tools.base import BaseTool, Tool
from backend.services.agent.tools.spawn import SpawnAgentTool
from backend.services.llm.llm_service import call_llm, stream_qwen

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 30   # 工程兜底，正常靠模型停止 + autocompact 收敛（CC 是 while(true)）
TOOL_RESULT_PREVIEW_LIMIT = 500
COMPACT_TRIGGER_RATIO = 0.85   # estimate_tokens > token_budget * 该比例 时触发 compaction
DEFAULT_COMPACT_KEEP_LAST_N = 4


class Agent:
    """通用 Agent，支持 tool-use 主循环。

    两条公共调用入口：
    - ``run_stream(user_input, cancel_event=None)`` → AsyncGenerator[AgentEvent]
      给 QA / Wiki / Topic 这类需要事件流的消费者
    - ``run(user_input)`` → str
      便捷接口：消费 run_stream 取最终文本，保留给 module_service / SpawnAgentTool
    """

    def __init__(
        self,
        system_prompt: str,
        tools: list[BaseTool | Tool] | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        enable_thinking: bool | None = None,
        auto_spawn_agent: bool = True,
        token_budget: int | None = None,
        compactor: Compactor | None = None,
        compact_keep_last_n: int = DEFAULT_COMPACT_KEEP_LAST_N,
        reminder_after_n_tools: int | None = None,
        reminder_interval: int = 4,
        reminder_text: str | None = None,
    ) -> None:
        self._system_prompt = system_prompt
        self._max_iterations = max_iterations
        self._enable_thinking = enable_thinking
        self._context = Context(system_prompt)
        # autocompact 配置：仅当 token_budget 与 compactor 都给出时启用
        self._token_budget = token_budget
        self._compactor = compactor
        self._compact_keep_last_n = compact_keep_last_n
        # CC 风格的 system reminder：累计工具调用次数到达阈值后开始注入提醒
        # 仅当 reminder_after_n_tools 与 reminder_text 都给出时启用
        # reminder_text 支持 {n} 占位符，会被替换为当前累计工具调用数
        self._reminder_after_n_tools = reminder_after_n_tools
        self._reminder_interval = max(1, reminder_interval)
        self._reminder_text_template = reminder_text

        # 构建工具列表（auto_spawn_agent=True 时保持原有自动注入行为）
        # tools=None + auto=True → 默认仅含 SpawnAgentTool
        # tools=None + auto=False → 无工具
        # tools=[] → 无工具（纯对话模式）
        # tools=[..] + auto=True → 用户工具 + 自动注入 SpawnAgentTool
        # tools=[..] + auto=False → 用户工具，不注入 SpawnAgentTool（QA 走这条）
        if tools is None:
            if auto_spawn_agent:
                self._tools: list[BaseTool | Tool] = [SpawnAgentTool(parent_enable_thinking=enable_thinking)]
            else:
                self._tools = []
        else:
            self._tools = list(tools)
            if (
                auto_spawn_agent
                and self._tools
                and not any(isinstance(t, SpawnAgentTool) for t in self._tools)
            ):
                self._tools.append(SpawnAgentTool(
                    parent_tools=self._tools,
                    parent_enable_thinking=enable_thinking,
                ))

        self._tool_map: dict[str, BaseTool | Tool] = {t.name: t for t in self._tools}

        logger.info(
            "Agent initialized: %d tools (%s), max_iterations=%d",
            len(self._tools),
            ", ".join(self._tool_map.keys()),
            self._max_iterations,
        )

    async def run_stream(
        self,
        user_input: str,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """主循环 + 事件流。所有消费者（QA/Wiki/Topic）走这个接口。

        事件序列契约：
        - 一轮工具调用：``ToolUseStart → ToolResult`` × N → ``IterationEnd``
        - 完成：``Stop(reason="completed", final_text=<LLM 最终文本>)``
        - 撞迭代上限：``Stop(reason="max_iterations", final_text=<LLM 最后 content 或兜底>)``
        - 取消：``Stop(reason="cancelled", final_text="")``

        ``model_error`` / ``compact_failed`` 在阶段 5 / 阶段 4 才引入；当前 LLM 异常会从
        ``call_llm`` 直接向上抛，由调用方处理（与重构前 ``run()`` 行为等价）。
        """
        self._context.add_user(user_input)

        assistant_message: dict[str, Any] = {}
        run_started = time.monotonic()
        total_tool_calls = 0  # 累计工具调用次数（跨轮），驱动 system reminder 注入
        next_reminder_at = self._reminder_after_n_tools  # 下一次注入的阈值，None 表示禁用

        for iteration in range(self._max_iterations):
            if cancel_event is not None and cancel_event.is_set():
                yield Stop(reason="cancelled", final_text="")
                return

            logger.info(
                "Agent iter %d/%d start (elapsed=%.1fs)",
                iteration + 1, self._max_iterations, time.monotonic() - run_started,
            )

            # CC 风格的 system reminder：撞阈值时往 context 注入一条提醒
            if (
                next_reminder_at is not None
                and self._reminder_text_template is not None
                and total_tool_calls >= next_reminder_at
            ):
                reminder = self._reminder_text_template.format(n=total_tool_calls)
                self._context.add_system_reminder(reminder)
                logger.info(
                    "Agent injected system_reminder after %d tool calls (next at %d)",
                    total_tool_calls, total_tool_calls + self._reminder_interval,
                )
                next_reminder_at = total_tool_calls + self._reminder_interval

            # autocompact 判定：撞预算且配置就位时摘要旧消息
            if self._should_compact():
                async for ev in self._do_compact():
                    yield ev
                    if ev.type == "stop":
                        return  # compact_failed → 终止

            # 1. 上下文组装
            messages = self._context.to_messages()
            tool_defs = self._get_tool_definitions()

            # 2. 模型决策（异常 → Stop(reason='model_error')，不再向上抛）
            llm_started = time.monotonic()
            try:
                assistant_message = await call_llm(
                    messages, tools=tool_defs, enable_thinking=self._enable_thinking,
                )
            except Exception as e:
                logger.exception("Agent LLM call failed at iter %d", iteration + 1)
                yield Stop(
                    reason="model_error",
                    final_text=f"[模型调用失败: {type(e).__name__}: {e}]",
                )
                return
            llm_elapsed = time.monotonic() - llm_started
            self._context.add_assistant(assistant_message)

            tool_calls = assistant_message.get("tool_calls")
            if not tool_calls:
                # 纯文本 → 完成
                content = assistant_message.get("content", "") or ""
                logger.info(
                    "Agent completed: iters=%d total=%.1fs (last_llm=%.1fs)",
                    iteration + 1, time.monotonic() - run_started, llm_elapsed,
                )
                yield Stop(reason="completed", final_text=content)
                return

            tool_names = [tc["function"]["name"] for tc in tool_calls]
            logger.info(
                "Agent iter %d llm=%.1fs → tool_calls=%s",
                iteration + 1, llm_elapsed, tool_names,
            )

            iteration_num = iteration + 1
            for tool_call in tool_calls:
                total_tool_calls += 1
                tool_id = tool_call.get("id", "")
                func = tool_call.get("function", {})
                name = func.get("name", "")
                raw_args = func.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except json.JSONDecodeError:
                    args = {}

                yield ToolUseStart(
                    iteration=iteration_num,
                    tool_id=tool_id,
                    name=name,
                    args=args,
                )

                tool_started = time.monotonic()
                result = await self._execute_tool(tool_call)
                _ok = _is_tool_result_ok(result)
                logger.info(
                    "Tool %s done in %.1fs ok=%s preview=%s",
                    name, time.monotonic() - tool_started, _ok,
                    result[:200].replace("\n", " "),
                )
                self._context.add_tool_result(
                    tool_call_id=tool_id,
                    name=name,
                    content=result,
                )

                yield ToolResult(
                    iteration=iteration_num,
                    tool_id=tool_id,
                    name=name,
                    ok=_is_tool_result_ok(result),
                    preview=_truncate(result, TOOL_RESULT_PREVIEW_LIMIT),
                    full=result,
                )

            yield IterationEnd(iteration=iteration_num)

        # 安全阀：撞迭代上限
        logger.warning(
            "Agent hit max iterations (%d) total=%.1fs",
            self._max_iterations, time.monotonic() - run_started,
        )
        fallback = assistant_message.get("content", "") or "[Agent 达到最大迭代次数]"
        yield Stop(reason="max_iterations", final_text=fallback)

    def inject_history(
        self,
        turns: list[tuple[str, list[dict], str]],
    ) -> None:
        """注入历史对话轮次，实现多轮对话记忆（CC 对齐）。

        每次 ask 新建 Agent 实例，历史轮次必须从外部加载后注入。
        上下文恢复逻辑委托给 Context.restore_turn，Agent 只负责按序调用。

        Args:
            turns: [(user_content, tool_chain, fallback_assistant), ...]
                   user_content       - 该轮用户消息文本
                   tool_chain         - 完整工具链（OpenAI 格式，deep 模式），空列表表示 fast 模式
                   fallback_assistant - tool_chain 为空时的 assistant 回答（fast 模式 / 旧消息）
        """
        for user_content, tool_chain, fallback_assistant in turns:
            self._context.restore_turn(user_content, tool_chain, fallback_assistant)

    def _should_compact(self) -> bool:
        """触发条件：compactor + token_budget 都配置，且当前估算 token 越过阈值。"""
        if self._compactor is None or self._token_budget is None:
            return False
        return self._context.estimate_tokens() > self._token_budget * COMPACT_TRIGGER_RATIO

    async def _do_compact(self) -> AsyncGenerator[AgentEvent, None]:
        """执行 autocompact：摘要 → 替换 Context → yield CompactBoundary。

        失败 → yield Stop(reason='compact_failed')，由主循环负责终止。
        """
        messages_for_summary = self._context.to_messages()[1:]  # 去掉 system
        logger.info(
            "Agent triggering compaction: %d msgs, ~%d tokens (budget=%d, ratio=%.2f)",
            len(messages_for_summary),
            self._context.estimate_tokens(),
            self._token_budget,
            COMPACT_TRIGGER_RATIO,
        )
        try:
            summary = await self._compactor.summarize(messages_for_summary)
        except Exception as e:
            logger.exception("Compaction failed")
            yield Stop(
                reason="compact_failed",
                final_text=f"[上下文压缩失败: {type(e).__name__}: {e}]",
            )
            return

        compacted_count = self._context.compact(
            summary, keep_last_n=self._compact_keep_last_n,
        )
        if compacted_count == 0:
            # 消息数 ≤ keep_last_n，没真正压缩；不 yield 事件，继续主循环
            return

        yield CompactBoundary(
            summarized_turns=compacted_count,
            new_input_tokens=self._context.estimate_tokens(),
        )

    async def run(self, user_input: str) -> str:
        """便捷接口：消费 run_stream 取最终文本。

        ⚠️ 必须与重构前 run() 行为等价——module_service / SpawnAgentTool 依赖此返回值。
        等价语义：
        - 完成 → LLM 最终 content（None 或 "" 都视为 ""）
        - 撞 max_iter → LLM 最后一次 content；若为空则 "[Agent 达到最大迭代次数]"
        - 模型调用失败 → **抛 RuntimeError**（与原 run() 让 call_llm 异常向上传一致）
          流式消费者请改用 run_stream() 接 ``Stop(reason='model_error')``
        - compact 失败 → 抛 RuntimeError（同上）
        """
        final = ""
        async for ev in self.run_stream(user_input):
            if ev.type == "stop":
                if ev.reason in ("model_error", "compact_failed"):
                    raise RuntimeError(ev.final_text)
                final = ev.final_text
        return final

    async def stream_run(self, user_input: str) -> AsyncGenerator[str, None]:
        """⚠️ 旧的纯对话接口（grep 显示无人调用），保留以防外部依赖；新代码用 run_stream。"""
        async for chunk in stream_qwen(self._system_prompt, user_input):
            yield chunk

    def _get_tool_definitions(self) -> list[dict] | None:
        if not self._tools:
            return None
        return [t.definition for t in self._tools]

    async def _execute_tool(self, tool_call: dict) -> str:
        """执行单个工具调用，返回结果字符串。"""
        func_info = tool_call["function"]
        tool_name = func_info["name"]
        raw_args = func_info.get("arguments", "{}")

        logger.info("Executing tool: %s args=%s", tool_name, raw_args[:300] if isinstance(raw_args, str) else json.dumps(raw_args, ensure_ascii=False)[:300])

        tool = self._tool_map.get(tool_name)
        if not tool:
            error_msg = f"未知工具: {tool_name}"
            logger.error(error_msg)
            return json.dumps({"error": error_msg}, ensure_ascii=False)

        try:
            kwargs = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError as e:
            error_msg = f"工具参数解析失败: {e}"
            logger.error("%s, raw_args=%s", error_msg, raw_args[:200])
            return json.dumps({"error": error_msg}, ensure_ascii=False)

        try:
            result = await tool.execute(**kwargs)
            if isinstance(result, str):
                return result
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            error_msg = f"工具 {tool_name} 执行失败: {type(e).__name__}: {e}"
            logger.exception(error_msg)
            return json.dumps({"error": error_msg}, ensure_ascii=False)


def _is_tool_result_ok(result_str: str) -> bool:
    """工具结果若为 JSON ``{"error": ...}`` 视为失败，否则成功。"""
    try:
        parsed = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return True
    if isinstance(parsed, dict) and "error" in parsed:
        return False
    return True


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"... ({len(s) - limit} more chars)"
