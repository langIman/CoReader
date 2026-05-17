"""Agent 对话上下文管理。

维护 OpenAI 格式的消息历史，提供组装和快照能力。
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# 单个 tool result 写入 context 时的硬上限（字符）。
# 防御任何工具误返回大块（如调用图全量、文件全文）打爆下一轮 LLM 调用上下文。
# 30KB 足够装下任意结构化结果的核心信息；超过的会被截断并附上说明。
MAX_TOOL_RESULT_CHARS = 30_000


class Context:
    """Agent 对话上下文。

    职责：
    1. 管理系统提示词（不可变）
    2. 追加 user/assistant/tool 消息
    3. 导出完整消息列表供 LLM 调用
    4. 估算 token 用量 + autocompact（阶段 4 新增）
    """

    def __init__(self, system_prompt: str) -> None:
        self._system_message: dict[str, Any] = {"role": "system", "content": system_prompt}
        self._messages: list[dict[str, Any]] = []

    def add_user(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})

    def add_assistant(self, message: dict[str, Any]) -> None:
        """追加助手消息（完整 message dict，可能含 tool_calls）。"""
        self._messages.append(message)

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        if len(content) > MAX_TOOL_RESULT_CHARS:
            logger.warning(
                "Tool result truncated: tool=%s original=%d chars cap=%d chars",
                name, len(content), MAX_TOOL_RESULT_CHARS,
            )
            head = content[: MAX_TOOL_RESULT_CHARS - 200]
            content = (
                f"{head}\n\n"
                f"[结果被截断：原始 {len(content)} 字符，超过 {MAX_TOOL_RESULT_CHARS} 上限。"
                f"请增加过滤参数或缩小查询范围后重试。]"
            )
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
        })

    def add_system_reminder(self, content: str) -> None:
        """注入 CC 风格的 system reminder（包在 user 消息的 <system-reminder> 标签里）。

        用 user role 而非 system role 是为了避免在对话中插入新 system 消息导致
        OpenAI 协议在多 system 时的歧义；标签让模型清楚这是工程层提示而非用户输入。

        典型用法：当工具调用次数过多时，提醒模型停止探索、立即输出最终答案。
        """
        self._messages.append({
            "role": "user",
            "content": f"<system-reminder>\n{content}\n</system-reminder>",
        })

    def to_messages(self) -> list[dict[str, Any]]:
        """导出完整消息列表（含 system）供 LLM 调用。返回深拷贝。"""
        return [copy.deepcopy(self._system_message)] + copy.deepcopy(self._messages)

    @property
    def turn_count(self) -> int:
        return sum(1 for m in self._messages if m["role"] == "user")

    # ---------------------------- 历史轮次恢复 ----------------------------

    def restore_turn(
        self,
        user_content: str,
        tool_chain: list[dict[str, Any]],
        fallback_assistant: str = "",
    ) -> None:
        """还原一轮历史对话（CC 对齐：保留完整工具链）。

        Args:
            user_content:       该轮用户消息文本
            tool_chain:         该轮助手侧完整消息链（OpenAI 格式）：
                                  assistant(tool_calls) → tool_result×N → assistant(final_text)
                                  deep 模式非空，fast 模式或旧消息传 []
            fallback_assistant: tool_chain 为空时的 assistant 回答文字（fast 模式用）

        调用方（Agent.inject_history）按轮次顺序依次调此方法，
        LLM 在下一轮能看到上轮的工具调用原始数据（CC 对齐核心）。
        """
        self.add_user(user_content)
        if tool_chain:
            for m in tool_chain:
                role = m.get("role")
                if role == "assistant":
                    self.add_assistant(m)
                elif role == "tool":
                    self.add_tool_result(
                        m.get("tool_call_id", ""),
                        m.get("name", ""),
                        m.get("content", ""),
                    )
        elif fallback_assistant:
            # fast 模式 / 旧消息：仅注入最终文字回答
            self.add_assistant({"role": "assistant", "content": fallback_assistant})

    # ---------------------------- 阶段 4：autocompact ----------------------------

    def estimate_tokens(self) -> int:
        """粗估输入 token 数：序列化字节 / 3（中英混合偏保守）。

        换精确 tokenizer 需额外依赖；本期 MVP 用字符数粗估。精确化属优化（§7）。
        """
        total = len(json.dumps(self._system_message, ensure_ascii=False, default=str))
        for m in self._messages:
            total += len(json.dumps(m, ensure_ascii=False, default=str))
        return total // 3

    def compact(self, summary: str, keep_last_n: int = 4) -> int:
        """用 ``summary`` 替换 ``_messages[:-keep_last_n]``。system 不动。

        Returns: 被压缩掉的消息条数。

        约束：
        - 若现有消息数 ≤ keep_last_n，不做任何事并返回 0
        - 摘要以 user 消息形式注入（带显式 ``[历史上下文已压缩]`` 标记），保证
          OpenAI 协议下消息序列合法（不会出现孤儿 tool_result）
        """
        if len(self._messages) <= keep_last_n:
            return 0

        compacted_count = len(self._messages) - keep_last_n
        # 取最近 keep_last_n 条；若起点是孤儿 tool_result 会破协议——简单丢弃直到第一条 user/assistant
        tail = self._messages[-keep_last_n:]
        while tail and tail[0].get("role") == "tool":
            tail = tail[1:]
            compacted_count += 1

        summary_msg = {
            "role": "user",
            "content": f"[历史上下文已压缩，以下是要点]\n\n{summary}",
        }
        self._messages = [summary_msg] + tail
        logger.info(
            "Context compacted: %d messages → 1 summary + %d tail",
            compacted_count + len(tail), len(tail),
        )
        return compacted_count
