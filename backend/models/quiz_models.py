"""Quiz 自测功能数据模型。

对应 QUIZ_FEATURE_PLAN.md §2.1。三种出题模式（history/page/project）共用一套结构。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class QuizMode(str, Enum):
    history = "history"
    page = "page"
    project = "project"


class QuizOption(BaseModel):
    """单个选项。"""

    key: str              # "A" | "B" | "C" | "D"
    text: str
    explanation: str      # 正确说为什么对，错误说哪里错了


class QuizCodeRef(BaseModel):
    """题目对应的代码位置（可选）。"""

    file: str
    line_start: int
    line_end: int


class QuizQuestion(BaseModel):
    """单道题。"""

    id: str = ""               # save_question 后由 DAO 回填
    session_id: str = ""
    index: int                 # 0–9
    question_text: str
    options: list[QuizOption]
    correct_key: str           # "A" | "B" | "C" | "D"
    code_ref: QuizCodeRef | None = None


class QuizSession(BaseModel):
    """测验会话元数据（不含题目）。"""

    id: str
    project_name: str
    mode: QuizMode
    source_id: str | None = None     # page 模式填 wiki_page_id；history/project 不使用
    title: str
    status: Literal["generating", "ready", "done"]
    score: int = 0
    answered_count: int = 0
    created_at: str


class QuizSessionDetail(QuizSession):
    """测验会话详情（含全部题目和已提交答案）。"""

    questions: list[QuizQuestion] = Field(default_factory=list)
    answers: dict[int, str] = Field(default_factory=dict)   # question_index → chosen_key


# ------------------------ 请求体 ------------------------


class QuizGenerateRequest(BaseModel):
    """POST /api/quiz/generate 请求体。"""

    project_name: str
    mode: QuizMode
    source_id: str | None = None     # page 模式必填


class QuizAnswerRequest(BaseModel):
    """POST /api/quiz/{session_id}/answer/{index} 请求体。"""

    chosen_key: str
