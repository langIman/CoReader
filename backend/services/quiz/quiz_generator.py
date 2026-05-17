"""Quiz 出题引擎：复用 Agent Loop + 7个 QA 工具。

对应 QUIZ_FEATURE_PLAN.md §4.2。三种模式（history/page/project）的差异仅在
预注入到 user_message 的上下文上，Agent 自行决定调哪些工具。

输出：``Stop.final_text`` 为完整 10 题 JSON 数组，由 ``parse_questions()`` 解析。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncGenerator

from backend.models.quiz_models import (
    QuizCodeRef,
    QuizGenerateRequest,
    QuizMode,
    QuizOption,
    QuizQuestion,
)
from backend.services.agent.agent import Agent
from backend.services.agent.compactor import LLMCompactor
from backend.services.agent.events import AgentEvent
from backend.services.agent.tools.get_call_edges import GetCallEdgesTool
from backend.services.agent.tools.get_file_content import GetFileContentTool
from backend.services.agent.tools.get_modules import GetModulesTool
from backend.services.agent.tools.get_summaries import GetSummariesTool
from backend.services.agent.tools.get_symbols import GetSymbolsTool
from backend.services.agent.tools.search_code import SearchCodeTool
from backend.services.agent.tools.search_symbols import SearchSymbolsTool

logger = logging.getLogger(__name__)


# 工程兜底：迭代上限 + autocompact 阈值（与 QA deep 对齐）
QUIZ_MAX_ITERATIONS = 30
QUIZ_TOKEN_BUDGET = 20_000

# CC 风格的"探索预算" system reminder：
# 累计 8 次工具调用后开始注入；之后每 4 次再注入一次（12、16、20...）
# 让模型从"我再看一点"的循环里跳出来直接出题
QUIZ_REMINDER_AFTER_N_TOOLS = 8
QUIZ_REMINDER_INTERVAL = 4
QUIZ_REMINDER_TEXT = (
    "你已经累计调用工具 {n} 次。基于代码理解类任务的常规预算（4–8 轮足够），"
    "你现在应该已有充足的信息材料。**请立即停止读取文件/搜索代码，直接输出 10 道题的 JSON 数组**作为最终回答。\n"
    "继续探索不会得到新信息：你已经看过的文件再读一遍内容是一样的，"
    "重复调用搜索工具结果也是一样的。\n"
    "如果某些细节确实不知道，可以在题目里只考察你已确认的事实——10 道题足够，"
    "不需要每个细节都查清。"
)


QUIZ_SYSTEM_PROMPT = """你是一名代码库测验出题专家。请根据给定的代码库信息，出 **正好 10 道** 四选一选择题，用来测验用户对该代码库的理解。

# 高效调用工具（重要）

- **能并行就并行**：当你需要多个独立的查询时，**在同一次响应里返回多个 tool_use 块**，不要一次一个排队。例如要看 3 个模块的代码，同时发 3 次 `get_file_content`；要找 5 个符号，同时发 5 次 `get_symbols`。
- **节奏控制**：合理的探索节奏是 **4–6 轮**完成信息收集，第 1–2 轮摸全貌（modules / summaries），第 3–4 轮按需深入（symbols / file_content / call_edges），收齐后直接出题。
- **不要重复**：已经查过的内容不要再查；如果一轮的工具结果已能支撑一道题的依据，就把它写进题目里，别为了"再确认"重复调用。

# 出题原则

1. **题型多样**：覆盖架构归属、数据流程、设计决策、行为推断四类，避免全是事实记忆。
2. **答案有依据**：每道题的正确答案必须能在你调用工具看到的代码或文档中找到依据，禁止凭空发挥。
3. **干扰项要真实**：错误选项必须使用你在工具调用中见到过的真实类名、函数名、模块名或常量，**不要编造**。例如不要捏造"MemoryManager"这种实际不存在的名字。
4. **解析有针对性**：每个选项都要有解析——正确选项说为什么对（援引代码事实），错误选项说哪里错了（指出该名字真实的职责或常见混淆点）。
5. **`code_ref` 尽量给出**：题目相关的代码位置（文件 + 行号）有把握时填上，没把握时省略。

# 工作流程

1. 探索代码库（按"高效调用工具"原则并行收集），通常 4–6 轮足够。
2. 收集完毕后，**直接输出 10 道题的 JSON 数组**作为最终回答（不再调工具）。
3. 输出必须是合法 JSON，**不要包含 markdown 代码块标记**（不要写 ```json）。
4. 数组长度必须是 10，不多不少。

# 输出格式（严格）

```
[
  {
    "question": "题目文本",
    "options": [
      {"key": "A", "text": "选项文本", "explanation": "解析"},
      {"key": "B", "text": "选项文本", "explanation": "解析"},
      {"key": "C", "text": "选项文本", "explanation": "解析"},
      {"key": "D", "text": "选项文本", "explanation": "解析"}
    ],
    "correct_key": "B",
    "code_ref": {"file": "backend/services/agent/agent.py", "line_start": 42, "line_end": 58}
  },
  ... 共 10 个对象 ...
]
```
"""


def _build_tools(project_name: str) -> list:
    """复用 QA 的 7 件套（`qa_service._build_deep_tools` 的克隆，避免互相导入）。"""
    return [
        GetSummariesTool(project_name),
        GetModulesTool(project_name),
        GetSymbolsTool(project_name),
        GetCallEdgesTool(project_name),
        GetFileContentTool(project_name),
        SearchSymbolsTool(project_name),
        SearchCodeTool(project_name),
    ]


def _build_user_message(
    req: QuizGenerateRequest,
    qa_questions: list[str] | None,
    page_title: str | None,
    page_content: str | None,
) -> str:
    """根据模式拼初始 user message。

    - history: 注入用户历史问题列表
    - page:    注入 wiki 页面内容
    - project: 不注入，让 Agent 自行从模块入手探索
    """
    if req.mode == QuizMode.history:
        bullets = "\n".join(f"- {q}" for q in (qa_questions or []))
        return (
            f"# 任务\n\n用户在过去的对话中提出过以下疑惑：\n\n{bullets}\n\n"
            "请调用工具查阅相关代码和文档，针对这些疑惑点，出 10 道选择题，"
            "确认用户已经真正理解了背后的知识。"
            "题目应聚焦在用户曾困惑的概念以及相邻的关键概念。"
        )
    elif req.mode == QuizMode.page:
        title_line = f"# 任务\n\n请围绕以下 wiki 页面《{page_title or req.source_id}》出 10 道选择题。\n\n"
        return (
            title_line
            + f"## 页面内容\n\n{page_content or ''}\n\n"
            "请调用工具查阅页面引用的实际代码片段，确保题目有代码依据。"
            "题目应考察用户对该页面所述内容的理解程度。"
        )
    else:  # project
        return (
            f"# 任务\n\n请为项目 `{req.project_name}` 出 10 道全项目随机选择题，覆盖不同模块。\n\n"
            "建议工作流程：\n"
            "1. 先调用 `get_modules` 了解模块结构\n"
            "2. 挑 4-5 个有代表性的模块，分别用 `get_summaries`、`get_symbols`、`get_file_content` 深入查阅\n"
            "3. 利用见到的真实符号名作为干扰项\n"
            "题目要在不同模块之间分散，避免集中在单一模块。"
        )


async def generate(
    req: QuizGenerateRequest,
    qa_questions: list[str] | None = None,
    page_title: str | None = None,
    page_content: str | None = None,
) -> AsyncGenerator[AgentEvent, None]:
    """运行 Agent 出题，yield 原始 AgentEvent 流。由 quiz_service 消费转为 SSE。"""
    user_message = _build_user_message(req, qa_questions, page_title, page_content)

    agent = Agent(
        system_prompt=QUIZ_SYSTEM_PROMPT,
        tools=_build_tools(req.project_name),
        max_iterations=QUIZ_MAX_ITERATIONS,
        auto_spawn_agent=False,
        token_budget=QUIZ_TOKEN_BUDGET,
        compactor=LLMCompactor(),
        reminder_after_n_tools=QUIZ_REMINDER_AFTER_N_TOOLS,
        reminder_interval=QUIZ_REMINDER_INTERVAL,
        reminder_text=QUIZ_REMINDER_TEXT,
    )
    async for event in agent.run_stream(user_message):
        yield event


# ---------------------------- JSON 解析 ----------------------------


_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?|\n?```\s*$", re.IGNORECASE)


def parse_questions(final_text: str) -> list[QuizQuestion]:
    """把 Agent 输出的 JSON 数组解析为 ``QuizQuestion`` 列表。

    容错：剥掉可能的 markdown 代码块栅栏；不足 10 题原样返回；多于 10 题截断。
    解析失败抛 ``ValueError``，由 quiz_service 转成 error SSE。
    """
    text = final_text.strip()

    # 剥掉 ```json ... ``` 栅栏（如果模型不听话加了）
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text).strip()

    # 兜底：从首个 '[' 到末个 ']' 截取（防止前后有解释性文本）
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("题目 JSON 中找不到数组括号")
    text = text[start : end + 1]

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败：{e}") from e

    if not isinstance(raw, list):
        raise ValueError("顶层应是 JSON 数组")

    questions: list[QuizQuestion] = []
    for i, item in enumerate(raw[:10]):
        if not isinstance(item, dict):
            continue
        try:
            options = [QuizOption(**o) for o in item.get("options", [])]
            if len(options) != 4:
                logger.warning("Quiz item %d: expected 4 options, got %d", i, len(options))
                continue
            code_ref = None
            if isinstance(item.get("code_ref"), dict):
                try:
                    code_ref = QuizCodeRef(**item["code_ref"])
                except Exception:
                    pass
            q_text = item.get("question") or item.get("question_text") or ""
            correct_key = item.get("correct_key", "")
            if not q_text or not correct_key:
                logger.warning("Quiz item %d: missing question/correct_key", i)
                continue
            questions.append(QuizQuestion(
                index=i,
                question_text=q_text,
                options=options,
                correct_key=correct_key,
                code_ref=code_ref,
            ))
        except Exception as e:
            logger.warning("Quiz item %d failed validation: %s", i, e)

    if not questions:
        raise ValueError("没有解析出任何合法题目")

    # 重新对齐 index，避免中间被跳过的项导致空洞
    for new_idx, q in enumerate(questions):
        q.index = new_idx

    return questions
