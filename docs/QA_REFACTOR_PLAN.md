# Phase 2：Wiki 问答功能 —— 后端实施计划（v2）

> 这份文档只覆盖**后端**。前端在后端稳定后开新对话独立规划（沿用 Wiki 后端→前端的分阶段节奏）。
>
> 每节末尾留有"待讨论"点，欢迎推翻。

---

## 0. Context

CoReader 已完成阶段 1（Wiki 生成 + 浏览）。阶段 2 目标：在 Wiki 基础上做"针对项目的自然语言问答"。

**产品形态**：
- 用户在 Wiki 页面点 💬问答 → 右侧抽屉滑出
- 提问框上方有 **模式切换**：
  - **快速模式**：秒级响应。后端自动检索相关代码段并打包到 prompt，LLM 一次回答
  - **深度模式**：Agent 带工具循环。LLM 自己决定要搜什么、读什么代码，过程可见（工具调用时间线），再给出回答。慢但更适合抽象/多跳问题
- LLM 回答所依据的数据：
  1. Wiki 文档（概览/模块/文件页的摘要）
  2. AST 数据（符号、调用关系、导入）
  3. **原始源码片段**（BM25 检索相关符号的函数体原文）
- 答复里 `[xxx](#wiki:page_id)` 点击跳转到对应 Wiki 页
- 答复里 `[L12-L34](#code:ref_1)` 点击打开底部 CodeDrawer 定位源码
- 深度模式下抽屉内有工具调用时间线（可折叠），展示 Agent 的检索路径
- 对话持久化到 SQLite，刷新不丢；可切换历史对话

**已冻结的用户决策**：
1. 两种模式共存：**快速**（单次 RAG）+ **深度**（Agent 工具循环），UI 切换
2. 流式（SSE）
3. 右侧滑出抽屉（与底部 CodeDrawer 是独立元素）
4. 对话持久化到 SQLite
5. 深度模式流式策略：进度事件 + 伪流式最终文本，**不改 `agent.py`**
6. **检索子系统**：混合方案
   - 快速模式：BM25 预检索 Top-K **符号级**源码片段塞进 prompt
   - 深度模式：加 `search_symbols` / `search_code` 工具让 Agent 主动查

---

## 1. 总体数据流

### 1.1 快速模式
```
用户提问
   │
   ├─ BM25 预检索（见 §2.7）：
   │     对全项目符号建 BM25 索引 → Top-K 符号
   │     → 读每个符号的源码片段（AST line 范围）
   │
   ├─ 预先打包上下文：
   │     wiki index 大纲
   │   + 模块列表
   │   + Top-K 相关文件摘要
   │   + Top-K 相关符号的完整定义源码    ← 新增
   │
   ├─ stream_messages(ctx.to_messages())     ← ctx 由 QAContextBuilder 构造
   │   ↓
   │   SSE: start → token* → code_refs → done
   │
   └─ 持久化 assistant 消息
```

### 1.2 深度模式
```
用户提问
   │
   ├─ Agent 工具循环（qa_service 内重实现，不改 agent.py）：
   │     可用工具集：
   │       get_summaries / get_modules / get_symbols /
   │       get_call_edges / get_file_content
   │       + search_symbols(query)     ← 新增，BM25 搜符号
   │       + search_code(pattern)      ← 新增，关键词搜源码
   │
   │     第 N 轮：call_llm(messages, tools)
   │       ├─ 有 tool_calls → 执行工具
   │       │    SSE: tool_call → [执行] → tool_result
   │       │    messages 追加 tool 结果 → 继续下一轮
   │       └─ 纯文本 → 退出循环
   │              SSE: token*（伪流式分片最终文本）→ code_refs → done
   │
   └─ 持久化 assistant 消息（含 tool_events 记录）
```

典型 Agent 流程：
1. 用户问 "load_config 怎么实现的"
2. Agent → `search_symbols("load_config")` → 得到匹配的符号列表（含 file+line）
3. Agent → `get_file_content(file, start, end)` → 拿到源码原文
4. Agent → 生成最终回答，引用 `[L12-L34](#code:ref_1)`

（相关参数在 §2.8 / §2.9 章节已定）

---

## 2. 后端

### 2.1 文件结构

```
backend/
├── models/
│   └── qa_models.py                    # 新建
├── dao/
│   ├── database.py                     # 改：init_db 增加两张表
│   └── qa_store.py                     # 新建
├── services/
│   ├── qa/                             # 新建目录
│   │   ├── __init__.py
│   │   ├── qa_service.py               # 编排 fast/deep（只管流程，不管装配）
│   │   ├── context_builder.py          # QAContextBuilder：把检索结果装配成 Context
│   │   ├── retrieval.py                # BM25 检索引擎（纯函数）
│   │   └── code_refs.py                # 解析 LLM 产出的 code_refs JSON
│   ├── agent/tools/
│   │   ├── search_symbols.py           # 新建：BM25 按符号
│   │   └── search_code.py              # 新建：正则/关键词搜源码
│   └── llm/prompts/
│       └── qa_prompts.py               # 新建：QA_SYSTEM_PROMPT_FAST/DEEP
├── controllers/
│   └── qa_controller.py                # 新建
└── main.py                             # 改：注册 qa_controller.router

requirements.txt                        # 改：加 rank_bm25
```

### 2.2 复用（不改）
- `services/agent/agent.py`、`services/agent/tools/*`、`services/agent/context/base.py`（**Context 保持纯消息历史管理，不下沉业务**）
- `services/llm/llm_service.py`（`call_llm`）
- `dao/summary_store.py`、`dao/ast_store.py`、`dao/wiki_store.py`、`dao/database.py:get_connection`

### 2.2.1 需要小改的
- `services/llm/llm_service.py`：**新增** `stream_messages(messages: list[dict]) -> AsyncGenerator[str]`
  - 现有 `stream_qwen(system, user)` 只接两个字符串参数，无法承载 Context 构造的完整 messages
  - 新函数和现有 `call_llm` 对称，流式版，供快速模式使用
  - 现有 `stream_qwen` 保留不动（避免影响其他调用方）

### 2.3 数据模型 (`models/qa_models.py`)

```python
from pydantic import BaseModel
from typing import Literal, Any

QAMode = Literal["fast", "deep"]

class QARequest(BaseModel):
    project_name: str
    conversation_id: str | None = None   # None → 新建
    question: str
    mode: QAMode = "fast"

class QAMessage(BaseModel):
    id: int
    conversation_id: str
    role: Literal["user", "assistant"]
    content: str
    mode: QAMode | None = None           # assistant 才有
    tool_events: list[dict[str, Any]] = []   # deep 模式记录
    code_refs: dict[str, dict] = {}          # ref_id → {file, start_line, end_line, symbol?}
    created_at: str

class Conversation(BaseModel):
    id: str
    project_name: str
    title: str                           # 默认取首问题前 30 字符
    created_at: str
    updated_at: str

class ConversationDetail(Conversation):
    messages: list[QAMessage]
```

### 2.4 SQLite Schema

扩展 `dao/database.py:init_db`：

```sql
CREATE TABLE IF NOT EXISTS qa_conversations (
    id           TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    title        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qa_conv_project
    ON qa_conversations(project_name, updated_at DESC);

CREATE TABLE IF NOT EXISTS qa_messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL,
    role             TEXT NOT NULL,
    content          TEXT NOT NULL,
    mode             TEXT,
    tool_events_json TEXT,
    code_refs_json   TEXT,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES qa_conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_qa_msg_conv
    ON qa_messages(conversation_id, id);
```

### 2.5 DAO (`dao/qa_store.py`)

```python
def create_conversation(project_name: str, title: str) -> str: ...  # 返回新 id
def list_conversations(project_name: str) -> list[Conversation]: ...
def get_conversation(conversation_id: str) -> ConversationDetail | None: ...
def delete_conversation(conversation_id: str) -> bool: ...
def append_message(conversation_id: str, message: QAMessage) -> int: ...  # 返回 message id
def touch_conversation(conversation_id: str) -> None: ...  # 更新 updated_at
```

### 2.6 Prompts (`services/llm/prompts/qa_prompts.py`)

两套 prompt，共享一段"输出格式约定"。格式约定示例：

```
# 输出格式约定
1. 用 Markdown 写答复
2. 引用 Wiki 页面用 [显示文本](#wiki:page_id)
   - page_id 形如 file_backend_services_summary_service_py 或 module_xxx 或 overview
3. 引用代码段用 [L12-L34](#code:ref_1)
4. 回答末尾追加一个 fenced code block 形如：
   ```code_refs
   {
     "ref_1": {"file": "backend/main.py", "start_line": 12, "end_line": 34, "symbol": "init_app"}
   }
   ```
   - 每个用到的 #code:ref_x 都必须在这个 block 里
   - 如果没有代码引用，block 可省略或写空对象 {}
```

**QA_SYSTEM_PROMPT_FAST**：
```
你是 {project_name} 这个代码项目的知识库问答助手。
你已经读过整个项目的文档摘要。用户会问关于项目的问题，你要：
1. 先根据提供的「项目上下文」回答
2. 引用具体文件 / 函数时，用上述 Wiki / code 链接格式
3. 如果上下文不足，坦白说"从已有信息推测不出"，不要瞎编

[输出格式约定同上]
```

User prompt 模板（由 QAContextBuilder._build_fast 填充，对应 §2.9 的完整样例）：
```
## 项目上下文
### Wiki 导航大纲
{wiki_index_outline}

### 模块列表
{module_list}

### 相关文件摘要
{top_n_file_summaries}

### 相关源码片段（按 BM25 相关度排序）
{top_k_symbol_source_snippets}

## 用户问题
{question}
```

**QA_SYSTEM_PROMPT_DEEP**：
```
你是 {project_name} 项目的知识库问答助手。你可以调用以下工具深入查询代码：
- search_symbols(query): 按关键词搜函数/类（不知道具体路径时用这个）
- search_code(pattern): 正则/关键词搜源码（查字面量、错误信息等）
- get_modules(): 模块划分
- get_summaries(type): 按 file/folder/project 获取摘要
- get_symbols(file_path): 指定文件的函数/类符号
- get_call_edges(symbol_name): 调用关系
- get_file_content(file, start, end): 读源码片段

回答策略：
1. 问题涉及具体代码逻辑时，**务必调用 search_symbols/search_code 定位，然后用 get_file_content 读原文**
2. 不要凭摘要猜测实现——能读代码就读
3. 最多 {MAX_ITER} 轮工具调用，然后给出最终回答
4. 给出最终回答时不再调用工具
5. 引用格式同 [输出格式约定]

[输出格式约定同上]
```

**待讨论**：
- prompt 写英文还是中文？项目现有 prompt 基本是中文，保持中文
- code_refs 块是否真的是 LLM 最容易遵循的格式？需不需要改成 XML？

### 2.7 检索子系统 (`services/qa/retrieval.py`)

> **为什么独立于 Agent 工具？** "检索"在本系统里分两件事：
> 1. **BM25 语义检索引擎**（本节）——纯函数，给文本返 Top-K 符号
> 2. **Agent 工具壳**（§2.8）——把能力按 `BaseTool` 协议暴露给 LLM
>
> 拆开的理由：
> - 快速模式是 Python 主动调检索拼 prompt，不走 Agent 循环；如果把 BM25 逻辑写在工具文件里，快速模式会被迫依赖 Tool 类
> - 工具壳只是适配层（参数 schema + JSON 序列化）；将来换向量检索只动 `retrieval.py`，工具不碰
> - `search_code`（grep 字面检索）和 BM25 完全两条路，本不应共享引擎

两种模式共用的 BM25 检索层（`rank_bm25`，纯 Python 零 native 依赖）。

#### 2.7.1 索引单位：符号

**直接复用现有 `SymbolDef`**（`models/graph_models.py:7`，已持久化到 `symbols` 表），不新建 dataclass。每个 `SymbolDef` 形成一个 document：

| SymbolDef 字段 | 在检索中的用途 |
|---|---|
| `name` | 检索文本主体 |
| `kind` | 检索文本（"function"/"class" 等） |
| `file` | 检索文本 + 用来查文件摘要 + 读源码定位 |
| `line_start` / `line_end` | 读源码片段的行范围 |
| `docstring` | 检索文本（高权重信号） |
| `qualified_name` | 作为文档 id |

拼检索文本：
```python
def _symbol_to_search_text(d: SymbolDef, file_summary: str | None) -> str:
    parts = [d.name, d.kind, d.file]
    if d.docstring: parts.append(d.docstring)
    if file_summary: parts.append(file_summary)
    return " ".join(parts)
```

> **为什么不把源码正文放进索引？** BM25 对长文本权重容易被稀释。符号名 + docstring + 文件摘要已经高度指向性，源码按命中再读即可。

#### 2.7.2 索引构建与缓存

```python
class SymbolIndex:
    def __init__(self, symbols: list[SymbolDef], bm25: BM25Okapi): ...

    @classmethod
    def build(cls, project_name: str) -> "SymbolIndex":
        ast_model = load_project_ast(project_name)          # 已有 DAO
        file_summaries = {
            s["path"]: s["summary"]
            for s in get_summaries_by_type(project_name, "file")
        }
        symbols = list(ast_model.definitions.values())
        tokenized = [
            _tokenize(_symbol_to_search_text(s, file_summaries.get(s.file)))
            for s in symbols
        ]
        return cls(symbols, BM25Okapi(tokenized))

    def top_k(self, query: str, k: int = 10) -> list[tuple[SymbolDef, float]]:
        scores = self.bm25.get_scores(_tokenize(query))
        return sorted(zip(self.symbols, scores), key=lambda x: -x[1])[:k]
```

读源码片段时，通过 `SymbolDef.file + line_start + line_end` 从 `file_store`（已有在内存里）或直接读磁盘取行范围。

缓存策略：
- 进程内 dict：`{project_name: (index, project_hash)}`
- 若 project_hash 变化 → 失效重建
- 构建耗时：1000 符号级别 <100ms，完全可接受不必预计算

#### 2.7.3 tokenize 策略

中英混合：
```python
def _tokenize(text: str) -> list[str]:
    # 1. 驼峰/下划线拆分：initApp → [init, app]；load_config → [load, config]
    # 2. 中文按字符切
    # 3. 统一小写，去停用词（常见英文停用词 + "的/了/是"等中文）
    ...
```

#### 2.7.4 对外接口

```python
def retrieve_symbols_for_question(
    project_name: str,
    question: str,
    k: int = 10,
) -> list[SymbolDef]:
    """仅返回 SymbolDef 列表（供 search_symbols 工具用）。"""

def retrieve_symbols_with_source(
    project_name: str,
    question: str,
    k: int = 8,
    max_lines_per_symbol: int = 80,
) -> list[dict]:
    """返回 [{"symbol": SymbolDef, "source_code": str}]。
    source_code = 从源文件抓 line_start..line_end 的行范围，超长则截断加 '# ... (truncated)'。
    供快速模式打包使用。"""
```

### 2.8 新增两个 Agent 工具

> 两个工具本质不同：`search_symbols` 是**语义检索**（调 §2.7 BM25 引擎），`search_code` 是**字面检索**（re grep，和 BM25 无关）。分两个工具是为了让 LLM 通过 description 明确选用场景——语义检索容错函数名/docstring 措辞，字面检索处理字符串/配置 key/错误信息。

#### `services/agent/tools/search_symbols.py`

```python
class SearchSymbolsTool(BaseTool):
    name = "search_symbols"
    description = (
        "按关键词语义搜索项目符号（函数/类）。返回 Top-K 匹配的符号，"
        "含 file_path 和 line 范围。用于定位你要读的代码。"
        "比 get_symbols 更适合用户问题抽象时——只有关键词，不知道具体文件。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "自然语言或关键词"},
            "top_k": {"type": "integer", "default": 10, "maximum": 20},
        },
        "required": ["query"],
    }
    async def execute(self, *, query: str, top_k: int = 10):
        project_name = get_project_name()
        index = get_or_build_index(project_name)
        results = index.top_k(query, k=top_k)
        return [{"name": d.name, "kind": d.kind, "file": d.file,
                 "line_start": d.line_start, "line_end": d.line_end,
                 "score": round(score, 2)} for d, score in results]
```

#### `services/agent/tools/search_code.py`

```python
class SearchCodeTool(BaseTool):
    name = "search_code"
    description = (
        "正则/关键词搜索项目源码，类似 grep。返回匹配行的文件+行号+片段。"
        "用于查找字符串字面量、注释、非符号命中（比如配置 key、错误信息）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python 正则或纯字符串"},
            "is_regex": {"type": "boolean", "default": False},
            "max_results": {"type": "integer", "default": 20, "maximum": 50},
        },
        "required": ["pattern"],
    }
    async def execute(self, *, pattern, is_regex=False, max_results=20):
        # 遍历 file_store 里的源码，re.finditer 或纯字符串 in 匹配
        # 返回 [{file, line, snippet: <匹配行前后各 1 行>}]
        ...
```

**待讨论**：
- `search_code` 要不要直接调 ripgrep subprocess？快但多一个系统依赖。MVP 我倾向纯 Python + `re` + `file_store` 现有数据
- 两个工具都需要 `project_name`。工具里通过 `get_project_name()` 拿还是构造时注入？现有工具全是前者，保持一致

### 2.9 QAContextBuilder (`services/qa/context_builder.py`)

> **设计动机**：项目以 Agent 为核心，两种模式应该统一经由 `Context` 这一消息容器。但 `Context` 本身（`services/agent/context/base.py`）是通用 Agent 原语，不能感知 BM25/wiki/summary。于是多加一层 Builder：**Builder 知道业务，Context 保持纯**。

```python
class QAContextBuilder:
    """把检索结果 + 项目知识装配成可供 LLM 消费的 Context。"""

    def __init__(self, project_name: str, question: str, mode: QAMode):
        self.project_name = project_name
        self.question = question
        self.mode = mode

    def build(self) -> Context:
        if self.mode == "fast":
            return self._build_fast()
        return self._build_deep()

    def _build_fast(self) -> Context:
        """快速模式：一次性塞满。wiki 大纲 + 模块 + 文件摘要 + Top-K 符号源码。"""
        ctx = Context(QA_SYSTEM_PROMPT_FAST.format(project_name=self.project_name))

        # 一次性检索
        top_symbols = retrieve_symbols_with_source(
            self.project_name, self.question,
            k=8, max_lines_per_symbol=80,
        )
        related_files = {s["symbol"].file for s in top_symbols}
        file_summaries = get_summaries_by_type(self.project_name, "file")
        ranked = _rerank_file_summaries(
            file_summaries, self.question, related_files
        )[:20]

        wiki_doc = load_wiki_document(self.project_name)
        enriched = USER_TEMPLATE.format(
            outline=_render_index_outline(wiki_doc.index),
            modules=_render_modules(wiki_doc),
            file_summaries=_render_summaries(ranked),
            code_snippets=_render_symbol_source_section(top_symbols),
            question=self.question,
        )
        ctx.add_user(enriched)
        return ctx

    def _build_deep(self) -> Context:
        """深度模式：只给原问题。检索让 Agent 自己通过 search_symbols / search_code 完成。"""
        ctx = Context(QA_SYSTEM_PROMPT_DEEP.format(
            project_name=self.project_name,
            max_iter=MAX_ITER_DEEP,
        ))
        ctx.add_user(self.question)
        return ctx
```

**qa_service 变薄**：
```python
async def _fast_stream(req, yield_event):
    ctx = QAContextBuilder(req.project_name, req.question, "fast").build()
    full = []
    async for chunk in stream_messages(ctx.to_messages()):  # 新增的流式函数
        full.append(chunk)
        await yield_event("token", {"delta": chunk})
    return "".join(full), []

async def _deep_stream(req, yield_event):
    ctx = QAContextBuilder(req.project_name, req.question, "deep").build()
    # ...主循环（和 §2.8 相同，直接操纵这个 ctx）
```

**收益**：
- 两种模式走同一个 Context 抽象，未来加多轮历史/prompt 注入只改 Builder
- `qa_service` 只管流程编排，不碰装配细节
- `Context` 原语保持不动，其他 Agent 场景不受影响

快速模式装配后的 user message 段落样例：

```markdown
## 项目上下文

### Wiki 导航大纲
- 概览
- 核心模块
  - 数据服务层 (backend/services/)
  - ...

### 模块列表
- 数据服务层: backend/services/summary_service.py, ...
- ...

### 相关文件摘要
#### backend/services/summary_service.py
> 对代码文件生成 LLM 摘要，支持截断与重试。

...

### 相关源码片段（按问题相关度排序）
#### backend/services/summary_service.py::summarize_file  (L45-78)
```python
def summarize_file(path: Path, ast_skeleton: str) -> str:
    """摘要单个文件..."""
    prompt = ...
    return call_qwen(SYSTEM_PROMPT, prompt)
```

#### backend/dao/summary_store.py::upsert_summary  (L12-30)
```python
...
```

## 用户问题
{question}
```

### 2.10 Token 预算

符号级 Top-8 + 每段 ≤80 行约 640 行源码，平均每行 80 字符 = 51KB ≈ 15K tokens。加摘要/大纲 ~20K tokens。Qwen 上下文窗口 32K+ 够用。

兜底：
- 如果符号定义单体超 80 行，截前 80 行并加 `# ... (truncated)`
- 总字符数若 >40K，动态减小 `k_symbols`

**待讨论**：
- `k_symbols=8` 够不够？
- 80 行单符号截断会不会切掉关键逻辑？或许按 token 而非行数截

### 2.11 深度模式工具循环 (`services/qa/qa_service.py::_deep_stream`)

不改 `agent.py`，在 qa_service 内用 `Context` + `call_llm` + 工具实例手写循环：

**两道安全阀**：
1. **工具调用轮数上限** `MAX_ITER_DEEP = 6`
2. **累计 input token 预算** `DEEP_TOKEN_BUDGET = 20_000`：超了就把下一轮 `call_llm` 的 `tools` 参数设为 `None`，强制 LLM 用已有信息给出最终回答（而不是再调工具）

Context 在循环中持续膨胀（每轮追加 assistant 消息 + tool_result 消息），OpenAI 协议每次都要重发全部 messages。两道安全阀都是为了防止失控。

```python
MAX_ITER_DEEP = 6
DEEP_TOKEN_BUDGET = 20_000          # 输入 token 预算（粗估）

def _estimate_tokens(messages: list[dict]) -> int:
    # 粗估：中英文混合，1 token ≈ 2~3 字符，取 /3 偏保守
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // 3

async def _deep_stream(req, yield_event):
    ctx = QAContextBuilder(req.project_name, req.question, "deep").build()

    tools = [
        GetSummariesTool(), GetModulesTool(),
        GetSymbolsTool(), GetCallEdgesTool(),
        GetFileContentTool(),
        SearchSymbolsTool(),
        SearchCodeTool(),
    ]
    tool_map = {t.name: t for t in tools}
    tool_defs = [t.definition for t in tools]
    tool_events = []

    for i in range(MAX_ITER_DEEP):
        messages = ctx.to_messages()

        # —— 安全阀 2：token 预算用尽，强制关闭工具 ——
        over_budget = _estimate_tokens(messages) > DEEP_TOKEN_BUDGET
        current_tools = None if over_budget else tool_defs
        if over_budget:
            await yield_event("budget_exhausted", {
                "tokens_est": _estimate_tokens(messages),
                "budget": DEEP_TOKEN_BUDGET,
            })

        msg = await call_llm(messages, tools=current_tools)
        ctx.add_assistant(msg)

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            # 终止 → 伪流式
            content = msg.get("content", "")
            async for chunk in _pseudo_stream(content, chunk_size=20):
                await yield_event("token", {"delta": chunk})
            return content, tool_events

        # 预算用尽时 tools=None，LLM 不应再产生 tool_calls；
        # 若仍有（罕见），忽略并要求用已有信息回答
        if over_budget:
            # 把 tool_calls 当作噪音丢弃，下一轮继续 tools=None
            continue

        for tc in tool_calls:
            name = tc["function"]["name"]
            args = json.loads(tc["function"].get("arguments", "{}"))
            await yield_event("tool_call", {
                "iteration": i + 1, "name": name,
                "args_preview": _truncate(args, 200),
            })
            tool = tool_map.get(name)
            if not tool:
                result = {"error": f"未知工具: {name}"}
            else:
                try:
                    result = await tool.execute(**args)
                except Exception as e:
                    result = {"error": f"{type(e).__name__}: {e}"}
            result_str = json.dumps(result, ensure_ascii=False, default=str)
            ctx.add_tool_result(tool_call_id=tc["id"], name=name, content=result_str)
            await yield_event("tool_result", {
                "iteration": i + 1, "name": name,
                "ok": "error" not in (result if isinstance(result, dict) else {}),
                "preview": _truncate(result_str, 500),
            })
            tool_events.append({
                "iteration": i + 1, "name": name,
                "args": args, "result_preview": _truncate(result_str, 500),
            })

    # —— 安全阀 1：到达迭代上限 ——
    return "[达到工具调用上限，回答可能不完整]", tool_events
```

SSE 协议里新增一个事件 `budget_exhausted`（见 §2.14），前端可选地显示提示："AI 正在基于已收集的信息给出回答..."

`_pseudo_stream`：
```python
async def _pseudo_stream(content: str, chunk_size: int = 20, delay: float = 0.02):
    for i in range(0, len(content), chunk_size):
        yield content[i:i+chunk_size]
        await asyncio.sleep(delay)
```

**待讨论**：
- `MAX_ITER_DEEP = 6` 合适吗？（Agent 默认 10，QA 要更克制）
- 伪流式的 `chunk_size=20` / `delay=0.02` 需要调参
- 工具执行失败时，是直接塞错误字符串给 LLM 让它自己重试，还是立即中断？当前是塞错误字符串

### 2.12 公共 wrapper：`qa_service.answer`

`_fast_stream` / `_deep_stream` 都写成 **async generator**（直接 yield SSE 事件元组），`answer` 用 `async for` 委派它们的流出，避免中间缓冲。

```python
async def answer(req: QARequest) -> AsyncGenerator[tuple[str, dict], None]:
    """编排入口。yield (event_name, payload) 流。
    Controller 负责把元组序列化成 SSE 帧。"""
    # 1. 确定 conversation_id（新建或复用）
    conv_id = req.conversation_id or create_conversation(
        req.project_name, req.question[:30],
    )

    # 2. 持久化 user 消息
    user_msg_id = append_message(conv_id, QAMessage(
        role="user", content=req.question,
        conversation_id=conv_id, created_at=_now(),
    ))

    # 3. 发 start
    yield ("start", {
        "conversation_id": conv_id,
        "user_message_id": user_msg_id,
        "mode": req.mode,
    })

    # 4. 委派流式事件；同时收集最终 content 和 tool_events
    collected_content: list[str] = []
    tool_events: list[dict] = []
    inner = _fast_stream(req) if req.mode == "fast" else _deep_stream(req)
    async for event in inner:
        name, payload = event
        if name == "__final__":          # 内部 sentinel，不发给客户端
            collected_content.append(payload["content"])
            tool_events = payload["tool_events"]
            continue
        yield event

    # 5. 解析 code_refs 块
    clean_content, code_refs = parse_code_refs("".join(collected_content))

    # 6. 持久化 assistant 消息
    assistant_msg_id = append_message(conv_id, QAMessage(
        role="assistant", content=clean_content, mode=req.mode,
        tool_events=tool_events, code_refs=code_refs,
        conversation_id=conv_id, created_at=_now(),
    ))
    touch_conversation(conv_id)

    # 7. 发 code_refs + done
    yield ("code_refs", {"refs": code_refs})
    yield ("done", {"assistant_message_id": assistant_msg_id})
```

对应的 `_fast_stream` / `_deep_stream` 签名改成：
```python
async def _fast_stream(req: QARequest) -> AsyncGenerator[tuple[str, dict], None]:
    ctx = QAContextBuilder(req.project_name, req.question, "fast").build()
    buf: list[str] = []
    async for chunk in stream_messages(ctx.to_messages()):
        buf.append(chunk)
        yield ("token", {"delta": chunk})
    yield ("__final__", {"content": "".join(buf), "tool_events": []})
```

深度模式同理：原本 `await yield_event(...)` 改成 `yield (name, payload)`，循环结束时 yield 一个 `__final__`。

### 2.13 Controller (`controllers/qa_controller.py`)

```python
router = APIRouter(prefix="/api/qa", tags=["qa"])

@router.post("/ask")
async def post_ask(req: QARequest):
    async def sse_stream():
        try:
            async for event_name, payload in answer(req):
                yield f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
    return StreamingResponse(sse_stream(), media_type="text/event-stream")

@router.get("/conversations")
async def list_convs(project_name: str) -> list[Conversation]: ...

@router.get("/conversations/{conv_id}")
async def get_conv(conv_id: str) -> ConversationDetail: ...

@router.delete("/conversations/{conv_id}")
async def delete_conv(conv_id: str) -> dict: ...
```

### 2.14 SSE 协议完整清单

| event | payload 示例 | 何时推送 |
|---|---|---|
| `start` | `{"conversation_id": "abc", "user_message_id": 12, "mode": "deep"}` | 每次 ask 最先发 |
| `tool_call` | `{"iteration": 1, "name": "get_summaries", "args_preview": {"summary_type": "file"}}` | 深度模式每次调工具前 |
| `tool_result` | `{"iteration": 1, "name": "get_summaries", "ok": true, "preview": "[{...}, ...]"}` | 深度模式每次工具完成后 |
| `budget_exhausted` | `{"tokens_est": 20450, "budget": 20000}` | 深度模式 token 预算超限，强制收敛 |
| `token` | `{"delta": "这个文件"}` | 最终回答流式分片（两种模式都有） |
| `code_refs` | `{"refs": {"ref_1": {"file": "a.py", "start_line": 1, "end_line": 10}}}` | done 之前一次 |
| `done` | `{"assistant_message_id": 13}` | 流结束 |
| `error` | `{"message": "..."}` | 异常中止 |

---

## 3. 前端（本文档不覆盖）

前端单独开对话规划，与后端解耦。对前端会提出的**硬性要求**已在后端设计中预留：

- `POST /api/qa/ask` 返回 SSE（`text/event-stream`），事件协议见 §2.14
- 会话列表、消息详情、删除走 `GET/DELETE /api/qa/conversations[/{id}]`
- 助手消息的 `code_refs` 通过 SSE `code_refs` 事件单独下发，前端拿到后塞给 `MarkdownRenderer` 渲染 `#code:` 锚点
- `mode` 字段 `"fast" | "deep"` 由前端按用户选择传入，后端据此路由

前端规划要等到后端 curl 验证闭环通过后再开，避免边改后端边改前端。

---

## 4. 实施顺序（建议）

1. **schema + DAO**：改 `database.py` + 建 `qa_store.py` + `qa_models.py`
2. **检索层**：`retrieval.py`（BM25 索引 + 缓存） + tokenize
3. **Prompt + 装配 + code_refs 解析**：`qa_prompts.py` + `context_builder.py`（QAContextBuilder）+ `code_refs.py`
4. **llm_service 小改**：新增 `stream_messages(messages)`
5. **qa_service 快速模式**：打通 `/api/qa/ask` 流 + 持久化
6. **controller + main.py 注册**
7. **curl 验证快速模式 SSE**（含 `code_refs` 块解析）
8. **两个新 Agent 工具**：`search_symbols.py` + `search_code.py`
9. **qa_service 深度模式**：工具循环 + 进度事件 + 伪流式
10. **curl 验证深度模式**（工具调用能被触发、SSE 事件顺序正确）
11. **历史接口**：`GET/DELETE /api/qa/conversations[/{id}]`
12. **curl 完整回归**：快速/深度、历史、取消、重连，走完后进前端规划

> 9–10 步是重点调试阶段。先让快速模式稳定再做深度模式，能快速拿到一条可用路径，深度模式出问题时可以对比定位。

---

## 5. 验证路径（curl，后端独立闭环）

前置：后端起来，tinyapp 的 wiki 已生成。

1. **快速模式**
   ```bash
   curl -N -X POST /api/qa/ask -H 'Content-Type: application/json' \
     -d '{"project_name":"tinyapp","question":"main.py 做了什么?","mode":"fast"}'
   ```
   - 期望 SSE：`start → token* → code_refs → done`
   - 回答含至少一个 `#wiki:` 链接
   - 回答末尾的 ```code_refs 块被解析成 `code_refs` 事件

2. **深度模式**
   ```bash
   curl -N -X POST /api/qa/ask -H 'Content-Type: application/json' \
     -d '{"project_name":"tinyapp","question":"哪些函数调用了 load_config?","mode":"deep"}'
   ```
   - 期望 SSE：`start → tool_call → tool_result → (可能再循环) → token* → code_refs → done`
   - 至少出现一次 `search_symbols` 或 `get_call_edges` 工具调用

3. **历史接口**
   ```bash
   curl /api/qa/conversations?project_name=tinyapp
   curl /api/qa/conversations/<id>
   curl -X DELETE /api/qa/conversations/<id>
   ```

4. **会话延续**：第二次 ask 带 `conversation_id`，历史消息能读出来

5. **流中途断开**：`Ctrl+C` 杀 curl → 查库 `assistant` 消息未落盘（`done` 前不 append）

---

## 6. 风险与取舍

| 项 | 取舍 |
|---|---|
| 重实现 Agent 循环 ~50 行代码重复 | 接受，换来不动 `agent.py`；漂移后再抽公共 helper |
| 伪流式掩盖工具循环耗时 | 靠 `tool_call`/`tool_result` 事件维持感知 |
| 快速模式可能爆 context | 按 token 预算裁剪 Top-N；极端情况退化到只发模块列表 |
| LLM 可能不遵守 `code_refs` JSON 格式 | prompt 写死示例；解析器容错（缺失时降级为纯 Markdown） |
| 无向量检索 | MVP 先上 BM25，效果不够再加 embedding |
| BM25 索引每个项目进程内缓存 | 重启需重建；千级符号 <100ms 可接受 |
| 源码不入 BM25 索引 | 权重稀释会伤召回；靠符号名+docstring+文件摘要 + Agent 的 `search_code` 兜底 |
| SSE 中途断开如何处理 | 后端只在 `done` 才写 assistant 消息，半截丢弃 |
| 深度模式 Context 随工具轮数膨胀 | 两道安全阀：`MAX_ITER_DEEP=6` 限轮数，`DEEP_TOKEN_BUDGET=20000` 限输入 tokens，超阈值则关闭 tools 强制收敛 |
| Token 估算是粗估（字符数/3） | MVP 可接受；以后要精确就接真实 tokenizer |

---

## 7. 未覆盖 / 下一步

- **前端实现**：后端闭环通过后，开新对话单独规划（Wiki 抽屉 / QA 面板 / 消息气泡 / 工具时间线 / SSE 消费）
- **搜索/检索升级**：阶段 2.1 可加向量检索（embedding + FAISS/SQLite 向量扩展）
- **对话分享/导出**：暂不做
- **多轮上下文记忆**：当前每次 ask 只传当前轮问题给 LLM（不传历史消息）——**这一点需要讨论**，是否把同一会话前几轮消息也传给 LLM
- **Token 统计 / 费用展示**：暂不做
- **阶段 3 自测**：基于 qa_messages 表可直接做，schema 已考虑兼容

---

## 待你确认的关键问题（后端）

1. **多轮记忆**：同一会话连续提问时，后续问题要不要把前面的 Q&A 也传给 LLM 做上下文？（影响成本和 prompt 结构）
2. **深度模式迭代上限**：6 轮合适吗？
3. **检索 Top-K**：快速模式 `k_symbols=8`、`k_file_summaries=20` 够不够？单符号截断 80 行合理吗？
4. **BM25 索引**：索引单位是"符号"，检索文本 = name + docstring + 文件摘要（源码不入）。你认可吗？
5. **search_code 实现**：纯 Python `re` 遍历 `file_store` vs 调 ripgrep subprocess？
6. **code_refs 用 fenced code block 而不是 XML / 结构化 JSON response**，你觉得 OK 吗？
7. **prompt 中英文**：保持中文（和 Wiki 一致）？
