# 项目上传后的数据生成阶段清单

按时间顺序列出"项目上传 → Wiki/QA 就绪"全过程中每个阶段产出的数据。每条都给一个真实例子（以本仓库 CoReader 自己为样本）。

---

## 阶段 0：上传与文件入库

**触发**：`POST /api/file/upload-project`
**调用**：`file_service.upload_project()` → `file_store.set_project_files()`
**存储**：内存（`_project_files: dict[str, str]`，进程级）

### 产出 1：`project_files`（路径 → 源码全文映射）

> 整个后续流程的"原料"，唯一的代码原文容器。

```python
# 实际形态
{
    "backend/main.py": "from fastapi import FastAPI\n...",
    "backend/services/wiki_service.py": "\"\"\"Wiki 生成编排层...\"\"\"\nimport asyncio\n...",
    "backend/models/graph_models.py": "...",
    "frontend/src/App.tsx": "...",
    "README.md": "# CoReader\n...",
    "Makefile": "install:\n\tpip install -r backend/requirements.txt\n...",
    # ... 共 ~80 个文件
}
```

### 产出 2：`project_name`

```python
"CoReader"
```

---

## 阶段 1：项目初始化（清理 + AST 构建）

**触发**：上传成功后由 `init_service.initialize_project()` 自动调用
**步骤**：清空旧 SQLite 记录 → `get_or_build_ast()` 解析全部源码
**存储**：SQLite 表 `project_ast`（持久）+ 内存缓存

### 产出 3：`ProjectAST.definitions`（符号表）

> 项目中所有函数 / 类 / 方法的定义索引，wiki/qa 引用代码的事实底座。

```python
{
    "backend/services/wiki_service.py::generate_wiki": SymbolDef(
        qualified_name="backend/services/wiki_service.py::generate_wiki",
        name="generate_wiki",
        kind="async_function",
        file="backend/services/wiki_service.py",
        line_start=68,
        line_end=250,
        decorators=[],
        docstring="Eager 流水线：AST → 摘要 → 模块划分 → 大纲 → 并发生成 → 概览 → 落盘。",
        params=["project_name"],
        is_entry=False,
    ),
    "backend/main.py::app": SymbolDef(name="app", kind="function", is_entry=True, ...),
    # ... 共 ~600 个符号
}
```

### 产出 4：`ProjectAST.edges`（调用边）

> 所有跨函数调用站点，跨模块依赖的统计来源。

```python
[
    CallEdge(
        caller="backend/services/wiki_service.py::generate_wiki",
        callee_name="generate_outline",
        callee_resolved="backend/services/wiki/outliner.py::generate_outline",
        file="backend/services/wiki_service.py",
        line=123,
        call_type="direct",
    ),
    CallEdge(
        caller="backend/services/wiki_service.py::generate_wiki",
        callee_name="get_or_build_ast",
        callee_resolved="backend/utils/analysis/ast_service.py::get_or_build_ast",
        file="backend/services/wiki_service.py",
        line=86,
        call_type="direct",
    ),
    # ... 共 ~2000 条边
]
```

### 产出 5：`ProjectAST.modules`（文件级元数据）

```python
{
    "backend/services/wiki_service.py": ModuleNode(
        path="backend/services/wiki_service.py",
        line_count=337,
        symbol_count=4,
        imports=[
            "backend.config",
            "backend.dao.file_store",
            "backend.services.module_service",
            # ...
        ],
    ),
    # ... 每个 .py / .ts / .rs 一条
}
```

### 产出 6：`ProjectAST.entry_points`（入口函数列表）

```python
[
    "backend/main.py::app",
    "backend/main.py::root",
    "backend/controllers/file_controller.py::upload_project",
    # ... FastAPI 路由 / __main__ / CLI 入口
]
```

---

## 阶段 2：分层摘要生成

**触发**：`POST /api/summary/generate` 或被 wiki 流水线自动触发
**调用**：`summary_service.generate_hierarchical_summary()`
**LLM 调用次数**：~N_files + N_folders + 1
**存储**：SQLite 表 `summaries`（持久）

### 产出 7：文件级摘要（`type='file'`）

> 每个源码文件一条，wiki 模块页投喂给 LLM 的核心素材。

```python
{
    "path": "backend/services/wiki_service.py",
    "type": "file",
    "summary": "Wiki 生成编排层。负责幂等判断、确保 AST 与摘要就绪、并发调度模块/章节/专题页生成、最终装配概览页和导航索引树并持久化为 WikiDocument。",
    "project_name": "CoReader",
}
```

### 产出 8：文件夹级摘要（`type='folder'`）

```python
{
    "path": "backend/services/wiki",
    "type": "folder",
    "summary": "Wiki 生成相关的所有 generator 与工具：outliner 决定大纲，doc_collector 收集非代码线索，article/module/overview generator 各自负责一种页面类型，_postprocess 统一处理 LLM 输出的代码引用解析。",
    "project_name": "CoReader",
}
```

### 产出 9：项目级摘要（`type='project'`，最多一条）

```python
{
    "path": "",
    "type": "project",
    "summary": "CoReader 是面向 AI 生成代码的结构化代码审阅工具，结合 AST 静态分析、多 Agent LLM 系统和 ReactFlow 可视化。后端 FastAPI + SQLite，前端 React 19 + Vite，目前以 Wiki 文档生成 + 问答为主交互形态。",
    "project_name": "CoReader",
}
```

---

## 阶段 3：模块划分

**触发**：被 wiki 流水线在生成大纲前自动调用
**调用**：`module_service.generate_module_split()` → Agent + `ModuleSplitSkill`
**LLM 调用次数**：1-N 轮（Agent 自主多轮收集后给出最终 JSON）
**存储**：当前**未持久化**——返回值直接被 wiki 流水线消费

### 产出 10：模块划分结果

> 每个模块由 LLM 综合摘要 + AST 决定，是 wiki 模块页和章节页 prompt 里的核心素材。

```python
{
    "modules": [
        {
            "name": "Wiki 生成流水线",
            "description": "把项目从 AST + 摘要逐级烘焙为多页面 Wiki 文档",
            "paths": [
                "backend/services/wiki_service.py",
                "backend/services/wiki/outliner.py",
                "backend/services/wiki/doc_collector.py",
                "backend/services/wiki/article_generator.py",
                "backend/services/wiki/module_page_generator.py",
                "backend/services/wiki/overview_generator.py",
                "backend/services/wiki/_postprocess.py",
                "backend/services/wiki/page_ids.py",
            ],
        },
        {
            "name": "AST 静态分析",
            "description": "解析项目源码，构建调用图与入口点",
            "paths": [
                "backend/utils/analysis/call_graph.py",
                "backend/utils/analysis/entry_detector.py",
                "backend/utils/analysis/import_analysis.py",
                "backend/utils/analysis/ast_service.py",
                "backend/utils/analysis/ts_parser.py",
            ],
        },
        # ... 通常 5-12 个模块
    ]
}
```

---

## 阶段 4：Wiki 文档生成

**触发**：`POST /api/wiki/generate`
**调用**：`wiki_service.generate_wiki()` 编排
**存储**：SQLite 表 `wiki_documents`（持久，按 `project_hash` 幂等）

### 4.1 非代码数据收集

**调用**：`doc_collector.collect(project_files)`
**LLM 调用次数**：0（纯文本扫描）

#### 产出 11：`DocBundle`

```python
DocBundle(
    root_readme="# CoReader\n\nCoReader is a structured code review tool...",
    folder_readmes={
        "backend": "# Backend\n...",
        # ... 各子目录的 README
    },
    configs={
        "backend/requirements.txt": "fastapi==0.115.0\nuvicorn==0.32.0\n...",
        "frontend/package.json": "{ \"name\": \"frontend\", ... }",
        ".env.example": "QWEN_API_KEY=...\nQWEN_BASE_URL=...",
    },
    run_hints={
        "Makefile": "install:\n\tpip install -r backend/requirements.txt\n...",
        "backend/main.py": "from fastapi import FastAPI\nfrom backend.controllers...\n（前 50 行）",
        "frontend/package.json#scripts": '{ "dev": "vite", "build": "tsc -b && vite build" }',
    },
    stats=ProjectStats(
        total_files=82,
        total_lines=11543,
        language_distribution={"python": 41, "typescript": 23, "tsx": 12},
    ),
)
```

### 4.2 大纲生成

**调用**：`outliner.generate_outline()`
**LLM 调用次数**：1
**存储**：仅作为流水线中间值，落盘到各页面 metadata.brief

#### 产出 12：`OutlinePlan`

```python
OutlinePlan(
    chapters=[
        ChapterSpec(
            title="1. 系统数据流：从源码到 Wiki",
            brief="按时间顺序讲解项目上传 → AST → 摘要 → 模块划分 → 大纲 → 页面生成的完整链路，强调每一步如何把上一步的产物消化成自己的输入。",
        ),
        ChapterSpec(
            title="2. 多 Agent 与 Skill 体系",
            brief="解释 Agent 主循环 + Tool + Skill 的协作关系，展示如何用 Skill 包装一个具体能力（以 ModuleSplitSkill 为例）。",
        ),
        ChapterSpec(
            title="3. 缓存与持久化策略",
            brief="讲清楚 AST / 摘要 / Wiki 三层 SQLite 持久化的幂等机制，以及内存缓存如何避免重复计算。",
        ),
    ],
    topics=[
        TopicSpec(
            title="为什么模块页吃的是摘要而不是源码",
            brief="讨论"摘要级联"架构选择背后的取舍：token 预算、调用次数、信息保真度。",
        ),
        TopicSpec(
            title="跨模块调用统计的近似性",
            brief="解释 callee_resolved 解析失败时的处理、动态分发场景的盲点。",
        ),
    ],
)
```

### 4.3 模块页 / 章节页 / 专题页生成（并发）

**调用**：分别走 `module_page_generator` / `article_generator.generate_chapter_page` / `article_generator.generate_topic_page`
**LLM 调用次数**：N_modules + N_chapters + N_topics（受 `MAX_WORKER_CONCURRENCY=5` 限流）

#### 产出 13：单页 `WikiPage`（以模块页为例）

```python
WikiPage(
    id="module_0",
    type="module",
    title="Wiki 生成流水线",
    path=None,
    status="generated",
    content_md="""# Wiki 生成流水线

## 职责与边界
本模块把项目的静态分析结果与摘要烘焙成多页面的 Wiki 文档...

## 内部组成
| 文件 | 职责 |
| --- | --- |
| `backend/services/wiki_service.py` | 流水线编排器 |
| `backend/services/wiki/outliner.py` | 大纲决策 |
| ...

## 关键代码
[generate_wiki](#code:ref_0) 是流水线入口...
[generate_outline](#code:ref_1) 一次 LLM 调用决定章节...

## 跨模块关系
本模块输入来自 [AST 静态分析](#wiki:module_1)...
""",
    metadata=PageMetadata(
        outgoing_links=["module_1", "module_2"],
        code_refs={
            "ref_0": CodeRef(
                file="backend/services/wiki_service.py",
                start_line=68,
                end_line=250,
                symbol="generate_wiki",
            ),
            "ref_1": CodeRef(
                file="backend/services/wiki/outliner.py",
                start_line=56,
                end_line=95,
                symbol="generate_outline",
            ),
        },
        module_info=ModuleInfo(files=["backend/services/wiki_service.py", ...]),
        brief=None,
    ),
)
```

#### 章节页 / 专题页同样形态

```python
WikiPage(id="chapter_0", type="chapter", title="1. 系统数据流：从源码到 Wiki",
        content_md="...", metadata=PageMetadata(brief="按时间顺序讲解...", code_refs={...}))
```

### 4.4 概览页生成

**调用**：`overview_generator.generate_overview_page()`（依赖所有子页已完成）
**LLM 调用次数**：1

#### 产出 14：`overview` 页

```python
WikiPage(id="overview", type="overview", title="CoReader",
        content_md="# CoReader\n\n这是一个把 AI 生成的代码库...\n\n## 核心架构\n本项目按 ... 组织，关键章节见 [系统数据流](#wiki:chapter_0)...",
        metadata=PageMetadata(outgoing_links=["chapter_0", "module_0", ...]))
```

### 4.5 分类页与索引树（无 LLM）

**调用**：`wiki_service._build_category_page()` / `_build_index()`

#### 产出 15：3 个分类占位页

```python
[
    WikiPage(id="cat_architecture", type="category", title="核心架构", content_md=None),
    WikiPage(id="cat_modules", type="category", title="模块详解", content_md=None),
    WikiPage(id="cat_topics", type="category", title="专题深入", content_md=None),
]
```

#### 产出 16：导航索引树 `WikiIndex`

```python
WikiIndex(
    root="overview",
    tree={
        "overview": WikiIndexNode(title="CoReader", children=["cat_architecture", "cat_modules", "cat_topics"]),
        "cat_architecture": WikiIndexNode(title="核心架构", children=["chapter_0", "chapter_1", "chapter_2"]),
        "chapter_0": WikiIndexNode(title="1. 系统数据流：从源码到 Wiki", children=[]),
        # ...
        "cat_modules": WikiIndexNode(title="模块详解", children=["module_0", "module_1", ...]),
        "module_0": WikiIndexNode(title="Wiki 生成流水线", children=[]),
        # ...
        "cat_topics": WikiIndexNode(title="专题深入", children=["topic_0", "topic_1"]),
        # ...
    },
)
```

### 4.6 最终装配 `WikiDocument`

#### 产出 17：完整 Wiki 文档（持久化）

```python
WikiDocument(
    project_name="CoReader",
    project_hash="3f1a8c...（SHA256，用于幂等）",
    generated_at="2026-04-25T10:23:14Z",
    pages=[overview_page, *category_pages, *chapter_pages, *module_pages, *topic_pages],
    index=WikiIndex(...),
)
```

---

## 阶段 5：QA 问答（按需触发，独立流程）

**触发**：`POST /api/qa/ask`
**调用**：`qa_service` → 走 fast 或 deep 模式
**存储**：SQLite 表 `qa_history`

### 产出 18：QA 历史记录

```python
{
    "session_id": "uuid-...",
    "question": "Wiki 生成时怎么决定要不要重跑？",
    "answer": "通过 project_hash 幂等：对所有文件内容 SHA256...",
    "mode": "deep",  # 或 "fast"
    "tool_trace": [...],  # deep 模式记录的工具调用时间线
    "created_at": "...",
}
```

> 该流程不消费"模块划分"产物，但会读取 `ProjectAST` 和文件级摘要。

---

## 数据依赖速查

```
upload (project_files, project_name)
   │
   ├─► AST (definitions, edges, modules, entry_points)        [SQLite]
   │
   ├─► hierarchical summary (file/folder/project)             [SQLite]
   │      ▲
   │      └─ 依赖 project_files（要读源码做摘要）
   │
   ├─► module split (modules)                                 [内存，临时]
   │      ▲
   │      └─ 依赖 file/folder summaries + AST
   │
   ├─► DocBundle (readme/configs/run_hints/stats)             [内存，临时]
   │      ▲
   │      └─ 依赖 project_files
   │
   ├─► OutlinePlan (chapters/topics)                          [内存，临时；brief 进 page metadata]
   │      ▲
   │      └─ 依赖 modules + module_summaries + AST + DocBundle + project_summary
   │
   ├─► WikiPage × N (overview/chapters/modules/topics)        [SQLite]
   │      ▲
   │      └─ 依赖以上全部
   │
   └─► QA history (按需)                                      [SQLite]
          ▲
          └─ 依赖 AST + summaries + project_files
```

---

## 给"模块页方案1精修版"的启示

模块页生成时**已经能直接拿到**这些上下游产物：

| 输入字段（已有 + 新增） | 已经在哪个阶段产出？ | 是否在 wiki_service 里能拿到？ |
|---|---|---|
| `module_summary` / `file_summaries` | 阶段 2 | ✅ `get_summaries_by_type()` |
| `module_paths` | 阶段 3 | ✅ `module["paths"]` |
| `cross_module_interaction` | 阶段 1（边） + 阶段 3（归属） | ✅ `ast_model.edges` + `path_to_module_index` |
| `readme_snippet` | 阶段 4.1 | ✅ `doc_bundle.readme_for(path)` |
| **`symbols_text`（新增）** | 阶段 1（`ast_model.modules[path].definitions`） | ✅ 现成数据，零改动 |
| **`key_code_text`（新增）** | 阶段 0（`project_files[path]`） + 阶段 1（按 `line_start/end` 切片） | ✅ 都是已有数据，只需在 module_page_generator 里拼装 |

**结论**：方案1精修版需要的所有原料**当前流水线都已经生成完毕**，只需要在 `module_page_generator` 里增加两段拼装逻辑 + 在 `wiki_prompts.build_module_page_prompt` 增加两个字段，**不需要改动上游任何阶段**。
