# Quiz 自测功能实施计划

参考：NotebookLM 测验体验（选择题 + 每项解析 + "解释"跳转 QA）

---

## 一、功能概述

在 Wiki 页面 Header 工具栏加"出题"按钮，点击后右侧展开 **QuizDrawer**（与 QA 抽屉并列或替换），用户选择出题模式后开始生成，生成过程显示进度（已生成 X/10 题），完成后逐题作答。

### 三种出题模式

| 模式 | 触发入口 | 题目来源 |
|------|----------|----------|
| `history` — 针对对话历史 | QuizDrawer 内选择 | 用户历史 QA 问题 + wiki + 代码，针对曾经不懂的点出题 |
| `page` — 当前 Wiki 页面 | QuizDrawer 内选择（从 wiki 页打开时预选） | 当前页 `content_md` + 页面引用的代码片段 |
| `project` — 全项目随机 | QuizDrawer 内选择 | wiki overview + 所有模块摘要，随机覆盖多个模块 |

### 答题体验（对标 NotebookLM）

- 固定 10 题，一次一题，显示进度 1/10
- 选择后立即展开每个选项的解析（正确的说为什么对，错误的说为什么错）
- 左下角"解释"按钮 → **自动打开 QA 抽屉并发送预填问题**（不新增功能，直接调 `useQAStore.ask()`）
- 10 题结束后显示得分总结页

---

## 二、数据模型

### 2.1 Pydantic（`backend/models/quiz_models.py`）

```python
class QuizMode(str, Enum):
    history = "history"
    page = "page"
    project = "project"

class QuizOption(BaseModel):
    key: str              # "A" | "B" | "C" | "D"
    text: str
    explanation: str      # 为什么对/为什么错

class CodeRef(BaseModel):
    file: str
    line_start: int
    line_end: int

class QuizQuestion(BaseModel):
    id: str
    session_id: str
    index: int            # 0–9
    question_text: str
    options: list[QuizOption]
    correct_key: str
    code_ref: CodeRef | None = None

class QuizSession(BaseModel):
    id: str
    project_name: str
    mode: QuizMode
    source_id: str | None = None   # page 模式填 wiki_page_id；history 模式不使用（取全项目对话）
    title: str
    status: Literal["generating", "ready", "done"]
    score: int = 0
    answered_count: int = 0
    created_at: str

class QuizGenerateRequest(BaseModel):
    project_name: str
    mode: QuizMode
    source_id: str | None = None   # page/history 模式必填

class QuizAnswerRequest(BaseModel):
    chosen_key: str
```

### 2.2 SQLite（追加到 `backend/dao/database.py`）

```sql
CREATE TABLE IF NOT EXISTS quiz_sessions (
    id          TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    mode        TEXT NOT NULL,
    source_id   TEXT,
    title       TEXT,
    status      TEXT DEFAULT 'generating',
    score       INTEGER DEFAULT 0,
    answered_count INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quiz_questions (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    idx           INTEGER NOT NULL,
    question_text TEXT NOT NULL,
    options_json  TEXT NOT NULL,   -- JSON: list[QuizOption]
    correct_key   TEXT NOT NULL,
    code_ref_json TEXT,            -- JSON: CodeRef | null
    FOREIGN KEY (session_id) REFERENCES quiz_sessions(id)
);

CREATE TABLE IF NOT EXISTS quiz_answers (
    session_id     TEXT NOT NULL,
    question_index INTEGER NOT NULL,
    chosen_key     TEXT NOT NULL,
    is_correct     INTEGER NOT NULL,
    answered_at    TEXT NOT NULL,
    PRIMARY KEY (session_id, question_index)
);

CREATE INDEX IF NOT EXISTS idx_quiz_sessions_project
    ON quiz_sessions(project_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_quiz_questions_session
    ON quiz_questions(session_id, idx);
```

---

## 三、API 设计

### 3.1 端点列表

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/quiz/generate` | 生成测验，返回 SSE 流（逐题推送） |
| GET | `/api/quiz/{session_id}` | 拉取完整 session + 所有题目 + 已有答案 |
| POST | `/api/quiz/{session_id}/answer/{index}` | 提交第 index 题的答案 |
| GET | `/api/quiz/sessions?project_name=` | 历史测验列表 |
| DELETE | `/api/quiz/{session_id}` | 删除测验 |

### 3.2 SSE 流格式（`POST /api/quiz/generate`）

生成分两个阶段：Agent 工具调用阶段（探索代码库）+ 题目展示阶段（动画逐题出现）。

```
// 阶段一：Agent 工具调用，透传给前端作为状态提示
event: thinking
data: {"tool": "get_modules", "status": "正在扫描模块结构..."}

event: thinking
data: {"tool": "get_symbols", "status": "正在收集符号..."}

event: thinking
data: {"tool": "get_file_content", "status": "正在读取代码..."}

// 阶段二：Agent Stop 后，后端解析 JSON，逐题推送（动画用）
event: question
data: {"index": 0, "question_text": "...", "options": [...], "correct_key": "B", "code_ref": {...}}

event: question
data: {"index": 1, ...}

...（共10个 question 事件，间隔约100ms）

event: done
data: {"session_id": "uuid", "title": "核心架构测验"}

event: error
data: {"message": "..."}
```

前端在阶段一显示工具调用状态（"正在读取代码..."），阶段二逐题渲染出现的动画感，收到 `done` 后解锁答题。

---

## 四、后端实现

### 4.1 文件结构

```
backend/
  models/quiz_models.py          ← Step 1
  dao/quiz_store.py              ← Step 2
  dao/database.py                ← Step 3（追加建表）
  services/quiz/
    __init__.py
    quiz_generator.py            ← Step 4（LLM 出题逻辑）
    quiz_service.py              ← Step 5（编排 + SSE 生成）
  controllers/quiz_controller.py ← Step 6
  main.py                        ← Step 7（注册路由）
```

### 4.2 quiz_generator.py 核心逻辑

**出题引擎：复用现有 Agent Loop + 7个 QA 工具，不新增任何工具。**

```python
async def generate(req: QuizGenerateRequest) -> AsyncGenerator[AgentEvent]:
    user_message = _build_user_message(req)   # 三种模式各自预注入上下文

    agent = Agent(
        system_prompt=QUIZ_SYSTEM_PROMPT,
        tools=build_qa_tools(req.project_name),  # 复用现有7个工具
        max_iterations=15,
        auto_spawn_agent=False,
    )
    async for event in agent.run_stream(user_message):
        yield event   # 由 quiz_service 消费，转成 SSE
```

**三种模式的差异只在 user_message 预注入的上下文：**

```python
def _build_user_message(req: QuizGenerateRequest) -> str:
    if req.mode == "history":
        questions = qa_store.get_user_questions(req.project_name)  # 取历史问题列表
        return (
            f"用户曾提出以下问题：\n{questions}\n\n"
            "请调用工具查阅相关代码和文档，出10道选择题确认用户真正理解了这些疑惑点。"
        )
    elif req.mode == "page":
        page = wiki_store.get_page(req.source_id)                  # 取 wiki 页面内容
        return (
            f"请围绕以下 wiki 页面出10道选择题：\n\n{page.content_md}\n\n"
            "请调用工具查阅页面引用的代码片段，确保题目有代码依据。"
        )
    else:  # project
        return (
            f"请为项目 {req.project_name} 出10道覆盖不同模块的选择题。"
            "先调用 get_modules 了解全貌，再挑选有代表性的模块深入查阅，"
            "用真实符号名作为干扰项。"
        )
```

**Agent 的工具调用如何保证题目质量：**
- `get_symbols` / `search_symbols` → 找真实符号名做干扰项（不编造）
- `get_file_content` → 读实际代码，确保正确答案有代码依据
- `get_call_edges` → 为流程类题目提供调用链事实
- `get_modules` → project 模式下采样多个模块，保证覆盖面

**Agent 的 Stop.final_text 即完整 JSON，统一格式：**
```json
[
  {
    "question": "...",
    "options": [
      {"key": "A", "text": "...", "explanation": "这个选项混淆了..."},
      {"key": "B", "text": "...", "explanation": "正确。原因是..."},
      {"key": "C", "text": "...", "explanation": "这个选项...实际上..."},
      {"key": "D", "text": "...", "explanation": "这个选项...因为..."}
    ],
    "correct_key": "B",
    "code_ref": {"file": "backend/services/agent/agent.py", "line_start": 42, "line_end": 58}
  }
]
```

**quiz_service 消费 Agent 事件的流程：**

```python
async def _generate_stream(req, session_id):
    async for event in generator.generate(req):
        if isinstance(event, ToolUseStart):
            yield "thinking", {"tool": event.name, "status": _tool_status(event.name)}
        elif isinstance(event, Stop):
            if event.reason != "completed":
                yield "error", {"message": f"生成失败：{event.reason}"}
                return
            questions = parse_questions(event.final_text)
            for i, q in enumerate(questions):
                quiz_store.save_question(session_id, i, q)
                yield "question", q.dict()
                await asyncio.sleep(0.1)   # 动画节奏
            yield "done", {"session_id": session_id, "title": _make_title(req)}
```

---

## 五、前端实现

### 5.1 文件结构

```
frontend/src/
  types/quiz.ts                        ← Step 8
  services/quizApi.ts                  ← Step 9
  store/useQuizStore.ts                ← Step 10
  components/Quiz/
    QuizDrawer.tsx                     ← Step 11（主容器，含 ModeSelector）
    ModeSelector.tsx                   ← Step 12（三模式选择 + "开始出题"）
    GeneratingProgress.tsx             ← Step 13（已生成 X/10 题进度）
    QuizCard.tsx                       ← Step 14（单题卡片）
    OptionItem.tsx                     ← Step 15（选项行 + 解析展开）
    QuizResult.tsx                     ← Step 16（得分总结）
    QuizHistoryMenu.tsx                ← Step 17（历史测验侧边栏）
```

### 5.2 useQuizStore 核心状态

```typescript
interface QuizStore {
  open: boolean
  widthRatio: number            // 持久化，同 QA

  // 生成阶段
  generating: boolean
  thinkingStatus: string        // 阶段一：工具调用状态文本（"正在读取代码..."）
  generatedCount: number        // 阶段二：0–10，驱动"已生成 X/10 题"进度显示

  // 答题阶段
  session: QuizSession | null
  questions: QuizQuestion[]
  answers: Record<number, string>   // index → chosen_key
  currentIndex: number

  // actions
  openWithMode(mode: QuizMode, sourceId?: string, projectName: string): void
  startGenerate(req: QuizGenerateRequest): void  // 消费 SSE
  submitAnswer(index: number, key: string): void
  goTo(index: number): void
  reset(): void
  loadHistory(projectName: string): void
}
```

### 5.3 QuizCard 答题状态机

```
idle
  → 用户点击选项
selected (chosen_key 记录，但未展开解析)
  → 动画完成（或立即）
revealed (展开所有选项解析，高亮正确/错误，解锁"下一题"和"解释")
```

### 5.4 "解释"按钮实现

```typescript
// QuizCard.tsx
const { ask, setOpen: openQA } = useQAStore()

const handleExplain = () => {
  openQA(true)
  ask(
    `关于测验第 ${question.index + 1} 题：\n"${question.question_text}"\n\n` +
    `我选了 ${chosenKey}（${options.find(o => o.key === chosenKey)?.text}），` +
    `正确答案是 ${question.correct_key}。请结合代码详细解释。`,
    projectName
  )
}
```

### 5.5 Wiki 页面入口

在 Wiki 页面的 Header 工具栏加"出题"按钮，点击时：
1. 打开 QuizDrawer
2. 预选 `page` 模式，`sourceId` = 当前 `wikiPageId`
3. 用户可在 ModeSelector 里切换到其他模式

---

## 六、实施步骤

| Step | 内容 | 文件 |
|------|------|------|
| 1 | Pydantic 模型 | `models/quiz_models.py` |
| 2 | SQLite DAO | `dao/quiz_store.py` |
| 3 | 建表 | `dao/database.py` |
| 4 | Agent 出题引擎（3模式 user_message + QUIZ_SYSTEM_PROMPT） | `services/quiz/quiz_generator.py` |
| 5 | 编排 + SSE 推送 | `services/quiz/quiz_service.py` |
| 6 | HTTP 控制器 | `controllers/quiz_controller.py` |
| 7 | 注册路由 | `main.py` |
| 8 | TypeScript 类型 | `types/quiz.ts` |
| 9 | API 客户端 + SSE 解析 | `services/quizApi.ts` |
| 10 | Zustand store | `store/useQuizStore.ts` |
| 11 | QuizDrawer 主容器 | `components/Quiz/QuizDrawer.tsx` |
| 12 | ModeSelector | `components/Quiz/ModeSelector.tsx` |
| 13 | GeneratingProgress | `components/Quiz/GeneratingProgress.tsx` |
| 14 | QuizCard | `components/Quiz/QuizCard.tsx` |
| 15 | OptionItem（含解析） | `components/Quiz/OptionItem.tsx` |
| 16 | QuizResult 得分页 | `components/Quiz/QuizResult.tsx` |
| 17 | QuizHistoryMenu | `components/Quiz/QuizHistoryMenu.tsx` |
| 18 | Wiki Header 入口按钮 | Wiki 页面组件 |
| 19 | "解释"→ QA 联动 | `QuizCard.tsx` 调 `useQAStore.ask()` |

---

## 七、关键约束

- **出题引擎复用 Agent Loop**：`quiz_generator.py` 创建 `Agent`，挂载现有7个 QA 工具，`auto_spawn_agent=False`，`max_iterations=15`。不新增任何工具，不调用 `call_qwen()`。
- **三模式差异仅在 user_message**：quiz_service 根据模式预加载上下文（QA 历史问题 / wiki 页面内容）注入初始消息，Agent 自行决定调哪些工具。
- **干扰项来自真实符号**：Agent 调用 `get_symbols` / `search_symbols` 时自然接触到真实的类名/函数名，system prompt 要求从这些符号中选干扰项，不编造。
- **`code_ref` 字段可选**：LLM 未给出时不影响答题流程，有 `code_ref` 时前端可跳转到对应代码位置。
- **生成进度显示**：阶段一透传 `ToolUseStart` 事件为 `thinking` SSE（"正在读取代码..."），阶段二解析 JSON 后以 100ms 间隔逐题推送 `question` 事件（动画感）。
- **"解释"按钮**只在 `revealed` 状态（已作答后）显示，调 `useQAStore.ask()` 传预填问题，QA 那边零改动。
- **QuizDrawer 宽度**与 QA 抽屉一样支持拖拽，`widthRatio` 持久化到 localStorage。
- **history 模式前置检查**：project 下无 QA 对话时，ModeSelector 禁用该选项并提示"请先在 QA 中提问"。
