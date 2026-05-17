"""Agent 主循环的上下文压缩。

设计（QA_AGENT_LOOP_REFACTOR_PLAN.md §3 阶段 4）：
- ``Compactor`` Protocol：把旧消息序列摘要成一段文本
- ``LLMCompactor``：默认实现，单次轻量 Qwen 调用做摘要
- 摘要 prompt 简陋版即可——质量调优在 §7 优化集

调用关系：Agent 在每轮开头判定 ``context.estimate_tokens() > token_budget * 0.85``，
触发后调 ``compactor.summarize(messages)`` 拿到摘要，再 ``context.compact(summary)``
替换历史，最后 yield ``CompactBoundary`` 事件。
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from backend.services.llm.llm_service import call_llm
from backend.services.llm.prompts.qa_prompts import COMPACT_SUMMARY_PROMPT

logger = logging.getLogger(__name__)


@runtime_checkable
class Compactor(Protocol):
    """上下文压缩协议。

    实现方应当：
    - 输入 ``messages``：去掉 system 之后的对话历史（OpenAI 格式）
    - 输出：一段≤800 字的中文摘要文本
    - 失败时直接 raise；Agent 会 catch 后 yield Stop(reason='compact_failed')
    """

    async def summarize(self, messages: list[dict]) -> str: ...


class LLMCompactor:
    """默认 Compactor：单次 Qwen 调用做摘要。

    阶段 4 落地的"简陋版"——prompt 稳定/参数固定/不带 tools。后续要调优
    （多轮迭代、priority 策略保留关键消息等）属于 §7 优化集。
    """

    async def summarize(self, messages: list[dict]) -> str:
        body = _serialize_for_summary(messages)
        prompt_messages = [
            {"role": "system", "content": COMPACT_SUMMARY_PROMPT},
            {"role": "user", "content": body},
        ]
        resp = await call_llm(prompt_messages, tools=None, enable_thinking=False)
        content = resp.get("content") or ""
        if not content.strip():
            raise RuntimeError("Compactor 返回空摘要")
        logger.info(
            "Compactor produced summary: input=%d msgs (%d chars), output=%d chars",
            len(messages), len(body), len(content),
        )
        return content


def _serialize_for_summary(messages: list[dict]) -> str:
    """把 messages 序列化成可读文本喂给 LLM。

    每条消息按 role 渲染；tool_result 截断到 500 字符避免摘要本身太长。
    """
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        if role == "tool":
            name = m.get("name", "?")
            content = (m.get("content") or "")[:500]
            parts.append(f"[工具结果: {name}]\n{content}")
        elif role == "assistant":
            tool_calls = m.get("tool_calls")
            if tool_calls:
                names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
                parts.append(f"[助手调用工具: {', '.join(names)}]")
            content = m.get("content") or ""
            if content:
                parts.append(f"[助手]: {content[:500]}")
        elif role == "user":
            content = m.get("content") or ""
            parts.append(f"[用户]: {content[:500]}")
        else:
            parts.append(f"[{role}]: {(m.get('content') or '')[:200]}")
    return "\n\n".join(parts)
