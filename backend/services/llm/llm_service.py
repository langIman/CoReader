from collections.abc import AsyncGenerator

import asyncio
import httpx
import json
import logging
import time

from fastapi import HTTPException

from backend.config import (
    QWEN_API_KEY,
    QWEN_BASE_URL,
    QWEN_ENABLE_THINKING,
    QWEN_MODEL,
)

logger = logging.getLogger(__name__)


def _model_supports_thinking_switch(model: str) -> bool:
    """是否注入 enable_thinking 开关。

    实测支持：qwen3 / qwq / minimax 系列（含 MiniMax-M2.7 等大小写变体）。
    其他模型上注入会被网关报错或静默忽略，所以保留白名单。
    """
    normalized = model.strip().lower()
    return (
        normalized.startswith("qwen3")
        or normalized.startswith("qwq")
        or normalized.startswith("minimax")
    )


def _apply_qwen_options(payload: dict, enable_thinking: bool | None = None) -> None:
    """注入 qwen3 系列的 enable_thinking 开关。

    优先级：调用点显式参数 > QWEN_ENABLE_THINKING 环境变量 > 模型默认。
    检查 payload 里实际的 model（而非全局 QWEN_MODEL）——分层模型场景下
    fast 路径的实际 model 可能与全局不同。
    """
    effective = enable_thinking if enable_thinking is not None else QWEN_ENABLE_THINKING
    if effective is None:
        return
    actual_model = payload.get("model", QWEN_MODEL)
    if _model_supports_thinking_switch(actual_model):
        payload["enable_thinking"] = effective


# ---------------- 入口后的 tool_call.arguments 修复 -----------

def _normalize_tool_call_arguments(message: dict) -> None:
    """修复部分上游（实测 kimi-k2.6 经 sub2api 中转）把 tool_call.arguments 错拼成多个连续 JSON 对象。

    回包形如 ``{}{}`` 或 ``{}{"summary_type":"folder"}``——前缀那个空 ``{}`` 是上游
    把上一轮 tool_call 已闭合的对象漏拼了进来。标准 ``json.loads`` 会抛 "Extra data"，
    更糟的是这条 message 原样进入下一轮 messages 后 kimi 服务端校验失败直接 400。

    本函数用 ``raw_decode`` 把所有连续 JSON 对象解出 + dict-merge（后面覆盖前面），
    重写回 arguments，让 agent 端能正常 parse 且下一轮回传不再被服务端拒。
    无 tool_calls 或单个合法 JSON 时无副作用。
    """
    tool_calls = message.get("tool_calls")
    if not tool_calls:
        return
    decoder = json.JSONDecoder()
    for tc in tool_calls:
        fn = tc.get("function") or {}
        raw = fn.get("arguments")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            json.loads(raw)
            continue  # 合法 JSON，不动
        except json.JSONDecodeError:
            pass

        s = raw.strip()
        merged: dict = {}
        pos = 0
        try:
            while pos < len(s):
                obj, end = decoder.raw_decode(s, pos)
                if isinstance(obj, dict):
                    merged.update(obj)
                pos = end
                while pos < len(s) and s[pos] in " \t\r\n":
                    pos += 1
        except json.JSONDecodeError:
            logger.warning(
                "[call_llm] tool_call.arguments unparseable, left as-is: %r",
                raw[:200],
            )
            continue
        fn["arguments"] = json.dumps(merged, ensure_ascii=False)
        logger.warning(
            "[call_llm] normalized tool_call.arguments: %r → %s",
            raw[:120], fn["arguments"][:120],
        )


# ---------------- 网关瞬时错误重试（5xx / 524 等） -----------

_RETRY_STATUSES = {429, 502, 503, 504, 524}
_RETRY_DELAYS = (2.0, 5.0, 10.0)  # 短退避：偶发卡死靠多次重试摊开概率，不靠等
# 429: sub2api "All available accounts exhausted" — 账号池被打爆，等几秒池子恢复
# 502/503/504/524: 网关瞬时故障

# Per-attempt timeout：< CF 524 阈值（~124s），提前主动 kill 后立即 retry，
# 不等中转层走完 124s 才放弃。实测正常 file_summary 5-15s，>90s 几乎确定卡了。
_PER_ATTEMPT_TIMEOUT_S = 90.0


def _payload_diag(payload: dict) -> str:
    """生成 messages 的诊断摘要（role + content 长度），便于排查 4xx。"""
    msgs = payload.get("messages", [])
    return " | ".join(
        f"{m.get('role')}={len(m.get('content', '') or '')}c" for m in msgs
    )


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    payload: dict,
    *,
    op: str,
    per_attempt_timeout: float = _PER_ATTEMPT_TIMEOUT_S,
) -> httpx.Response:
    """非流式 POST，遇到 ``_RETRY_STATUSES`` 或单次超 per_attempt_timeout 自动重试。

    每次请求被 ``asyncio.wait_for`` 包裹：超时主动 kill 当前连接，立即重试，
    不被动等中转 CF 524（~124s）才放弃。

    per_attempt_timeout: 默认 90s（适合小 prompt）；大 prompt（如章节/专题页）
    调用方可传更大值（如 150s）避免正常生成被误杀。
    """
    last_resp: httpx.Response | None = None
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            resp = await asyncio.wait_for(
                client.post(url, json=payload, headers=headers),
                timeout=per_attempt_timeout,
            )
        except asyncio.TimeoutError:
            if attempt == len(_RETRY_DELAYS):
                logger.error(
                    "[%s] all %d attempts timed out (>%.0fs each); messages: %s",
                    op, len(_RETRY_DELAYS) + 1, per_attempt_timeout,
                    _payload_diag(payload),
                )
                raise HTTPException(
                    status_code=504,
                    detail=f"LLM 网关多次超时（{len(_RETRY_DELAYS)+1} × {per_attempt_timeout:.0f}s 均超时）",
                )
            delay = _RETRY_DELAYS[attempt]
            logger.warning(
                "[%s] per-attempt timeout (>%.0fs), kill+retry %d/%d after %.0fs",
                op, per_attempt_timeout, attempt + 1, len(_RETRY_DELAYS), delay,
            )
            await asyncio.sleep(delay)
            continue

        if resp.status_code not in _RETRY_STATUSES:
            if resp.status_code != 200:
                logger.error(
                    "[%s] HTTP %d (no retry); messages: %s",
                    op, resp.status_code, _payload_diag(payload),
                )
            return resp
        last_resp = resp
        if attempt == len(_RETRY_DELAYS):
            break  # 重试用尽
        delay = _RETRY_DELAYS[attempt]
        logger.warning(
            "[%s] gateway %d, retry %d/%d after %.0fs",
            op, resp.status_code, attempt + 1, len(_RETRY_DELAYS), delay,
        )
        await asyncio.sleep(delay)
    if last_resp is not None and last_resp.status_code != 200:
        logger.error(
            "[%s] HTTP %d after retries; messages: %s",
            op, last_resp.status_code, _payload_diag(payload),
        )
    return last_resp  # 最后一次失败的响应（让调用方走原本的错误处理）


async def call_qwen(
    system_prompt: str,
    user_prompt: str,
    enable_thinking: bool | None = None,
    model: str | None = None,
    per_attempt_timeout: float = _PER_ATTEMPT_TIMEOUT_S,
) -> str:
    """调用千问 API（非流式），返回完整文本。

    enable_thinking: qwen3 系列默认开 CoT，TTFT 高数倍。模板化任务（文档生成、
    摘要等）建议显式传 False；判断/推理类任务保持 None（走 env / 模型默认）。
    model: 指定模型；缺省使用 QWEN_MODEL。摘要类调用点可传 QWEN_FAST_MODEL。
    per_attempt_timeout: 单次尝试超时；大 prompt（章节/专题页）建议传 150.0。
    """
    if not QWEN_API_KEY:
        raise HTTPException(status_code=500, detail="未配置 QWEN_API_KEY 环境变量")

    url = f"{QWEN_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model or QWEN_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
    }
    _apply_qwen_options(payload, enable_thinking)

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await _post_with_retry(
            client, url, headers, payload,
            op="call_qwen", per_attempt_timeout=per_attempt_timeout,
        )
        if resp.status_code != 200:
            logger.error("LLM API error %d: %s", resp.status_code, resp.text[:500])
            raise HTTPException(
                status_code=502,
                detail=f"LLM API 错误 ({resp.status_code}): {resp.text[:200]}",
            )
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def stream_qwen(
    system_prompt: str,
    user_prompt: str,
    enable_thinking: bool | None = None,
    model: str | None = None,
) -> AsyncGenerator[str, None]:
    """调用千问 API（OpenAI 兼容格式），流式返回文本片段。

    model: 指定模型；缺省使用 QWEN_MODEL。
    """
    if not QWEN_API_KEY:
        raise HTTPException(status_code=500, detail="未配置 QWEN_API_KEY 环境变量")

    url = f"{QWEN_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model or QWEN_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": True,
    }
    _apply_qwen_options(payload, enable_thinking)

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                logger.error(
                    "[stream_qwen] HTTP %d: %s; messages: %s",
                    resp.status_code, body.decode()[:500], _payload_diag(payload),
                )
                raise HTTPException(
                    status_code=502,
                    detail=f"LLM API 错误 ({resp.status_code}): {body.decode()[:200]}",
                )

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    logger.debug("SSE chunk parse skipped: %s", data[:100])
                    continue


async def stream_messages(
    messages: list[dict],
    timeout: float = 120.0,
    enable_thinking: bool | None = None,
    model: str | None = None,
) -> AsyncGenerator[str, None]:
    """流式版 call_llm：接完整 messages 历史，逐 token 产出 content。

    对比 ``stream_qwen(system, user)``：那个只能接两段字符串，无法承载
    QAContextBuilder 装配出的完整 messages。本函数与 ``call_llm`` 对称。
    不返回 tool_calls（快速模式不跑工具循环）。
    model: 指定模型；缺省使用 QWEN_MODEL。
    """
    if not QWEN_API_KEY:
        raise HTTPException(status_code=500, detail="未配置 QWEN_API_KEY 环境变量")

    url = f"{QWEN_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model or QWEN_MODEL,
        "messages": messages,
        "stream": True,
    }
    _apply_qwen_options(payload, enable_thinking)

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                logger.error(
                    "[stream_messages] HTTP %d: %s; messages: %s",
                    resp.status_code, body.decode()[:500], _payload_diag(payload),
                )
                raise HTTPException(
                    status_code=502,
                    detail=f"LLM API 错误 ({resp.status_code}): {body.decode()[:200]}",
                )

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    logger.debug("SSE chunk parse skipped: %s", data[:100])
                    continue


async def call_llm(
    messages: list[dict],
    tools: list[dict] | None = None,
    timeout: float = 600.0,
    enable_thinking: bool | None = None,
    model: str | None = None,
) -> dict:
    """支持完整消息历史 + 工具定义的 LLM 调用。

    返回完整的 choice message dict，可能包含 content 和/或 tool_calls。
    model: 指定模型；缺省使用 QWEN_MODEL。
    """
    if not QWEN_API_KEY:
        raise HTTPException(status_code=500, detail="未配置 QWEN_API_KEY 环境变量")

    url = f"{QWEN_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict = {
        "model": model or QWEN_MODEL,
        "messages": messages,
        "stream": False,
    }
    _apply_qwen_options(payload, enable_thinking)

    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    started = time.monotonic()
    n_msgs = len(messages)
    n_tools = len(tools) if tools else 0
    logger.info(
        "call_llm start: model=%s messages=%d tools=%d timeout=%.0fs",
        payload["model"], n_msgs, n_tools, timeout,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await _post_with_retry(client, url, headers, payload, op="call_llm")
            elapsed = time.monotonic() - started
            if resp.status_code != 200:
                logger.error(
                    "call_llm failed in %.1fs: status=%d body=%s",
                    elapsed, resp.status_code, resp.text[:500],
                )
                raise HTTPException(
                    status_code=502,
                    detail=f"LLM API 错误 ({resp.status_code}): {resp.text[:200]}",
                )
            data = resp.json()
            logger.info("call_llm done in %.1fs", elapsed)
            message = data["choices"][0]["message"]
            _normalize_tool_call_arguments(message)
            return message
    except httpx.TimeoutException as e:
        logger.error(
            "call_llm timeout after %.1fs (limit=%.0fs): %s",
            time.monotonic() - started, timeout, type(e).__name__,
        )
        raise
