"""Skill 驱动的模块划分服务。"""

import logging

from fastapi import HTTPException

from backend.dao.file_store import get_project_name
from backend.dao.summary_store import get_summaries_by_type
from backend.services.agent import Agent
from backend.services.agent.skills.module_split import ModuleSplitSkill
from backend.utils.data_format import parse_llm_json

logger = logging.getLogger(__name__)


_JSON_RETRY_PROMPT = (
    '你上一条回复无法被解析为 JSON 对象。请立即重新输出结果，严格遵守：\n'
    '1. 整条消息必须是、且只能是一个 JSON 对象，不要 Markdown 代码块包裹、'
    '不要任何自然语言说明或前置思考\n'
    '2. 格式：{"modules": [{"name": "...", "description": "...", "paths": [...]}]}\n'
    '3. 不要再调用任何工具，直接给出最终 JSON'
)


async def generate_module_split() -> dict:
    """基于 Skill 驱动的 Agent 自主模块划分。"""
    project_name = get_project_name()
    if not project_name:
        raise ValueError("No project loaded")

    # 前置校验：无摘要时快速失败（不浪费 Agent 轮次）
    if not get_summaries_by_type(project_name, "folder") and not get_summaries_by_type(project_name, "file"):
        raise ValueError("No summaries found. Please generate summaries first.")

    # 用 Skill 驱动 Agent（Agent 自主收集数据、自主验证覆盖率）
    skill = ModuleSplitSkill(project_name)
    agent = Agent(system_prompt=skill.system_prompt, tools=skill.tools)
    user_input = skill.build_user_input({"project_name": project_name})
    raw = await agent.run(user_input)

    try:
        return parse_llm_json(raw)
    except HTTPException as e:
        # LLM 偶尔返回 prose 而不是 JSON（见 data_format.parse_llm_json）。
        # 复用 Agent 已有 Context 追问一轮：明确要求只吐 JSON、不再调工具。
        logger.warning(
            "Module split first output not JSON, retrying. head=%r",
            raw[:200] if raw else raw,
        )
        raw_retry = await agent.run(_JSON_RETRY_PROMPT)
        try:
            return parse_llm_json(raw_retry)
        except HTTPException as e2:
            logger.error(
                "Module split retry also failed. head=%r",
                raw_retry[:200] if raw_retry else raw_retry,
            )
            # 抛原始错误信息（带截断的 raw 供排查）
            raise e2 from e
