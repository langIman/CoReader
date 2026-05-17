# CoReader Wiki 化改造计划

> 将 CoReader 从"代码审查工具"转型为"读懂项目的知识库 + 问答平台"（类 DeepWiki）。

## 一、项目新定位

### 从 → 到
- **原定位**：结构化代码审查工具（选行 → 看评论）
- **新定位**：代码项目的"知识库 + 问答 + 自测"三合一平台

### 三阶段使用流程

| 阶段 | 用户行为 | 系统产出 |
|---|---|---|
| 1. 输入 → 文档 | 上传项目（代码 + README + 配置） | 生成可读性强的 Wiki 文档 |
| 2. 问答 → 定位 | 针对文档发问 | 回答 + 定位到具体代码 |
| 3. 输出 → 自测 | 验证学习效果 | 基于问答历史生成选择题 |

**MVP 范围**：阶段 1 + 2。阶段 3 后续迭代。

---

## 二、核心设计决策

| 决策点 | 结论 |
|---|---|
| 文档模式 | Wiki 模式（独立页面 + 跳转），概览页承担"线性叙事"入口 |
| 页面粒度 | **概览页 + 模块页 + 文件页**（三层，不做函数页） |
| 数据源 | 代码 + README + 配置文件 |
| 文档归属 | README 按路径归属；配置/依赖统一进概览页的"项目元信息"区 |
| 页面内容形式 | `content_md`（Markdown）为主体 + JSON 元数据（跳转/代码引用） |
| 流程图 | **暂时移除**，未来可能作为嵌入件回归 |
| 生成方式 | **异步 + 懒加载混合**：上传时生成概览/模块页，文件页按需生成 |
| 旧功能去留 | 明确删除（见第五节） |

---

## 三、数据结构（JSON Schema）

### 顶层
```typescript
WikiDocument {
  project_name: string;
  project_hash: string;       // 基于文件内容 hash，判断是否需要重新生成
  generated_at: timestamp;
  pages: WikiPage[];          // 所有页面扁平存放
  index: WikiIndex;           // 页面索引（前端导航树用）
}
```

### 页面（三种 type 共用结构）
```typescript
WikiPage {
  id: string;                 // "overview" | "module_<path>" | "file_<path>"
  type: "overview" | "module" | "file";
  title: string;
  path?: string;              // module/file 类型对应的实际路径
  status: "generated" | "pending";  // pending = 骨架已创建但 content_md 尚未生成
  content_md: string | null;  // pending 状态时为 null
  metadata: PageMetadata;
}
```

**生成策略**（懒加载混合方案）：
- `overview` / `module` 类型 → 上传时**立即生成**（status='generated'）
- `file` 类型 → 上传时**只建骨架**（status='pending'），用户首次访问时按需生成

### 元数据
```typescript
PageMetadata {
  outgoing_links: string[];   // 本页指向的其他页面 id

  code_refs: {                // Markdown 里 [text](#code:ref_id) 解析用
    [ref_id: string]: {
      file: string;
      start_line: number;
      end_line: number;
      symbol?: string;
    }
  };

  module_info?: {             // type = "module" 才有
    files: string[];          // 包含的文件 page_id
  };

  file_info?: {               // type = "file" 才有
    language: string;
    symbols: { name, kind, line }[];
    imports: string[];        // 依赖文件 page_id
  };
}
```

### 索引
```typescript
WikiIndex {
  root: "overview";
  tree: {
    [page_id: string]: {
      title: string;
      children: string[];
    }
  }
}
```

### Markdown 约定
- 页面跳转：`[summary_service](#wiki:file_backend_services_summary_service_py)`
- 代码引用：`[第 45-60 行](#code:ref_1)`（`ref_1` → `metadata.code_refs.ref_1`）

### 概览页特有内容（写在 content_md 里）
```markdown
# 项目名称

## 项目介绍
（来自根 README + LLM 重写）

## 整体架构
（LLM 叙事，描述各模块关系）

## 核心模块
- [模块 A](#wiki:module_a) —— 一句话介绍
- [模块 B](#wiki:module_b) —— 一句话介绍

## 项目元信息
### 技术栈
（来自 requirements.txt / package.json）

### 配置项
（来自 .env.example / config）

### 如何运行
（来自 Makefile / scripts / README）

## 典型数据流
（LLM 叙事）
```

---

## 四、存储设计（SQLite）

```sql
CREATE TABLE wiki_documents (
  project_name TEXT PRIMARY KEY,
  project_hash TEXT,
  generated_at TIMESTAMP,
  index_json TEXT              -- WikiIndex 序列化
);

CREATE TABLE wiki_pages (
  page_id TEXT,
  project_name TEXT,
  type TEXT,                   -- overview | module | file
  title TEXT,
  path TEXT,
  status TEXT,                 -- generated | pending（文件页首次访问前为 pending）
  content_md TEXT,             -- pending 时为 NULL
  metadata_json TEXT,
  PRIMARY KEY (project_name, page_id)
);
```

---

## 五、后端改造清单

### ✅ 保留（不动或微调）

| 模块 | 角色 |
|---|---|
| `utils/analysis/` | AST 管道：代码结构数据源 |
| `services/agent/` | 问答阶段（阶段 2）核心 |
| `services/llm/` | LLM 调用封装 |
| `services/file_service.py` | 文件上传处理 |
| `dao/database.py` / `ast_store.py` / `file_store.py` | 存储基础 |
| `controllers/file_controller.py` | 文件上传 API |

### 🔧 调整（角色变化）

| 模块 | 新角色 |
|---|---|
| `services/summary_service.py` | 从"最终输出" → **Wiki 生成的中间数据** |
| `services/module_service.py` | 从"独立功能" → **模块页的输入源**（可能补导出结构化字段） |
| `dao/summary_store.py` | 继续存摘要（被 Wiki 消费） |

### ➕ 新增

```
backend/
├── models/
│   └── wiki_models.py                   # Pydantic: WikiDocument / WikiPage
├── services/
│   ├── wiki_service.py                  # 编排总流水线
│   └── wiki/
│       ├── overview_generator.py        # 概览页生成
│       ├── module_page_generator.py     # 模块页生成
│       ├── file_page_generator.py       # 文件页生成
│       └── doc_collector.py             # 非代码数据收集：README 归属 / 配置文件 / 运行线索 / 项目统计
├── services/llm/prompts/
│   └── wiki_prompts.py                  # 三种页面 prompt 模板
├── dao/
│   └── wiki_store.py                    # 持久化
└── controllers/
    └── wiki_controller.py               # API 入口
```

### ❌ 删除

```
services/review_service.py
services/overview_service.py
services/detail_service.py
controllers/review_controller.py
controllers/graph_controller.py
controllers/summary_controller.py
controllers/module_controller.py
dao/graph_cache.py
services/llm/prompts/ 中 review/overview/detail 相关 prompt
models/graph_models.py（如果仅被流程图使用）
```

### `main.py` 调整
- 移除 `review` / `graph` / `summary` / `module` 的 router 注册
- 新增 `wiki` router

---

## 六、模块划分机制

模块页的生成依赖"模块划分"这个前置步骤。**不是简单按目录切分**，而是 Agent 驱动的智能划分。

### 6.1 划分流程

复用现有的 `services/agent/skills/module_split.py`（ModuleSplitSkill），Agent 有 5 个工具可用：

| 工具 | 用途 |
|---|---|
| `get_modules()` | 项目文件列表 + import 依赖图 |
| `get_summaries()` | 文件 / 文件夹摘要 |
| `get_symbols()` | 函数 / 类定义 |
| `get_call_edges()` | 调用关系 |
| `get_file_content()` | 读源文件 |

Agent 按"由粗到细"的分析流程自主决策：

```
第 1 层：全局概览
  ├─ 拿到文件列表 + import 依赖
  ├─ 读文件夹摘要
  └─ 读文件摘要
          ↓
第 2 层：深入分析（按需）
  ├─ 对职责不清晰的文件 → 看符号/调用关系
  └─ 必要时读文件全文
          ↓
第 3 层：决策
  └─ 按业务职责（而非目录）输出模块划分
```

### 6.2 划分的质量标准

**不限定模块数量**，改用质量标准让 Agent 自主判断（替换原有 prompt 中的"3-8 个"硬限制）：

1. **单一职责**：每个模块能用一句话说清楚做什么
2. **规模合理**：
   - 下限：至少 2 个相关文件（单文件模块应并入其他）
   - 上限：不超过 ~15 个文件（超过说明职责太宽）
3. **边界清晰**：模块间 import/调用关系明显少于模块内部
4. **对读者友好**：模块名能让新人大致猜到内容
5. **公共工具归集**：被多模块共同依赖的文件 → 单独一个"公共基础"模块
6. **全覆盖**：每个文件必须出现在且仅出现在一个模块中

**数量参考（非硬限）**：
- 小项目 (<20 文件)：2-4 个
- 中项目 (20-100 文件)：4-8 个
- 大项目 (>100 文件)：可能需要更多

> 现阶段**不支持嵌套子模块**，保持扁平结构，简化实现。未来若遇到超大项目需要嵌套，再扩展。

### 6.3 产出格式

```json
{
  "modules": [
    {
      "name": "业务服务层",
      "description": "处理核心业务逻辑和编排",
      "paths": ["backend/services/review_service.py", ...]
    },
    ...
  ]
}
```

### 6.4 在 Wiki 生成中的角色

`module_split` 的输出直接映射到 Wiki 的模块页：

```
module_split 输出的每个 module
      ↓
对应一个 WikiPage (type='module')
  ├─ page_id = "module_<sanitized_name>"
  ├─ title = module.name
  ├─ metadata.module_info.files = module.paths 里的文件对应的 page_id 列表
  └─ content_md ← module_page_generator LLM 生成
```

### 6.5 实施时需要调整的文件

- `backend/services/agent/skills/module_split.py` 的 system prompt：把"3-8 个"删掉，替换成质量标准章节（见 6.2）

---

## 七、生成流水线

### 7.1 总流程（懒加载混合方案）

#### 阶段 A：上传时（eager）
```
                  ┌──────────────────────────┐
                  │  文件上传 → AST 解析      │
                  └──────────────────────────┘
                                │
                ┌───────────────┴───────────────┐
                ↓                               ↓
        ┌───────────────┐              ┌───────────────┐
        │ ①文件摘要 LLM │  (并发)      │ doc_collector │
        │   (已有)       │              │ README/配置   │
        └───────┬───────┘              └───────┬───────┘
                ↓                               │
        ┌───────────────┐                      │
        │ ②模块摘要 LLM │  (并发)              │
        │   (已有)       │                      │
        └───────┬───────┘                      │
                │                               │
                ↓                               │
         ┌──────────────┐                      │
         │ ④模块页生成  │←─────────────────────┤
         │   (并发)      │                      │
         └──────┬───────┘                      │
                ↓                               │
         ┌──────────────┐                      │
         │ ⑤概览页生成  │←─────────────────────┘
         │   (单次)      │
         └──────┬───────┘
                ↓
  ┌───────────────────────────────┐
  │   持久化 wiki_store            │
  │  - overview/module: generated │
  │  - file 页:  骨架 + pending    │
  └───────────────────────────────┘
```

#### 阶段 B：用户首次访问某文件页时（lazy）
```
  GET /api/wiki/{project}/page/file_xxx
                │
                ↓
     ┌──────────────────┐
     │ status == pending? │
     └────┬─────────┬───┘
         是│         │否
          ↓         ↓
  ┌──────────────┐  │
  │ ③文件页生成  │   │
  │   即时触发    │   │
  └──────┬───────┘  │
         ↓           │
  ┌──────────────┐  │
  │ 更新 status   │   │
  │ = generated   │   │
  └──────┬───────┘  │
         ↓           ↓
    ┌──────────────────┐
    │   返回 WikiPage   │
    └──────────────────┘
```

**关键点**：
- **上传阶段**：概览页 + 所有模块页生成完就返回（成本低、耗时短）
- **文件页**：只建骨架（id / title / path / status='pending' / metadata 基础字段），**不调用 LLM**
- **访问触发**：用户点击文件页时即时生成，生成后永久缓存
- ④并发执行，⑤依赖④完成
- AST 数据和摘要数据**共享**给多个 LLM 调用，不重复计算
- LLM 拿到的**不是全量代码**，而是**结构化的摘要 + AST 数据**，成本可控

**成本估算**（200 文件的项目）：
- 原方案（全部提前）：200 + ~10 + 1 ≈ 211 次 LLM 调用（文件页生成）
- 当前方案：~10 + 1 ≈ 11 次立即 + 按实际访问量（通常 <50 次）
- 约节省 **70-80%** LLM 成本

---

### 7.2 每次 LLM 调用的输入明细

整个流水线共 5 种 LLM 调用（①②为已有阶段，③④⑤为新增）。

#### ① 文件摘要 LLM（已有，复用）
**调用位置**：`summary_service`
**调用频次**：每个代码文件一次，并发

| 输入 | 来源 |
|---|---|
| 文件路径 | 文件系统 |
| AST 骨架（函数签名、类签名、docstring） | AST 管道 |
| 必要时截断的代码 | 源文件 |

**LLM 任务**：产出这个文件做什么的简短摘要（2-3 句）。

---

#### ② 模块摘要 LLM（已有，复用）
**调用位置**：`module_service`
**调用频次**：每个模块一次

| 输入 | 来源 |
|---|---|
| 模块内所有文件摘要 | ①的产出 |
| 模块内文件结构 | AST 管道 |

**LLM 任务**：产出模块的一句话定位。

> ①②的产出存入 `summary_store`（SQLite），作为下游 Wiki 生成的原料。

---

#### ③ 文件页生成 LLM（新增，**懒加载**）
**调用位置**：`services/wiki/file_page_generator.py`
**调用频次**：用户首次访问该文件页时触发，生成后永久缓存

| 输入数据 | 来源 |
|---|---|
| 文件路径、语言 | 文件系统 |
| 文件摘要 | `summary_store`（①产出） |
| AST 符号详情（函数/类的签名、docstring、所在行） | AST 管道 |
| 调用关系（调用了谁、被谁调用） | `call_graph` |
| Imports（依赖的其他文件） | `import_analysis` |
| 核心代码片段（关键函数的前 N 行，可选） | 源文件 |

**LLM 任务**：
> "写这个文件页的 Markdown：开头说文件做什么，然后介绍关键函数/类，最后说它和其他文件的关系。跳转链接用 `[xxx](#wiki:...)`，代码引用用 `[L45-L60](#code:ref_1)`。"

**产出**：`content_md` + `code_refs`（填 metadata）。

---

#### ④ 模块页生成 LLM（新增）
**调用位置**：`services/wiki/module_page_generator.py`
**调用频次**：每个模块一次，并发

| 输入数据 | 来源 |
|---|---|
| 模块路径 | 文件系统 |
| 模块摘要 | `summary_store`（②产出） |
| 模块内所有文件摘要 | `summary_store`（①产出） |
| 跨模块调用关系（此模块与其他模块的交互） | `call_graph` 聚合 |
| 归属本模块的 README 片段 | `doc_collector` |

**LLM 任务**：
> "写这个模块的详细介绍页：职责、内部组成（列出子文件并链接到文件页）、对外交互（链接到其他模块）、关键数据流。"

**产出**：`content_md`（含跳转链接） + `module_info.files`（子文件 page_id 列表）。

---

#### ⑤ 概览页生成 LLM（新增）
**调用位置**：`services/wiki/overview_generator.py`
**调用频次**：只调用一次，最后执行

| 输入数据 | 来源 |
|---|---|
| 项目名 | 用户上传时指定 |
| 所有模块的摘要 + 模块页标题 | 上游产出 |
| 模块间依赖关系（高层视图） | `call_graph` 聚合 |
| 根 README 全文 | `doc_collector` |
| 项目元信息（`requirements.txt` / `package.json` / `.env.example` / `Makefile` 内容） | `doc_collector` |
| **运行线索**（Makefile、`package.json` 的 scripts 字段、`pyproject.toml` entry_points、Dockerfile CMD、约定入口文件 `main.py` / `app.py` / `index.ts` / `server.ts` 等的前 50 行） | `doc_collector` 增强 |
| **项目统计**（文件总数、代码语言分布） | 文件扫描阶段顺便统计 |

**LLM 任务**：
> "写项目概览页：项目介绍（融合 README）、整体架构叙事、核心模块导览（每个模块一句话 + 链接）、项目元信息（技术栈/配置/运行方式）、典型数据流（基于运行线索推断；如线索不足就坦白说'启动方式不明确'，不要瞎编）。"

**产出**：`content_md`（含模块导航链接）。

> **设计说明**：不使用 `entry_detector.py`（规则硬匹配不可靠）。改用"喂给 LLM 人类为人类准备的线索"（README、Makefile、scripts、约定入口文件），让 LLM 自己推断典型数据流。

---

### 7.3 数据流汇总

```
┌─── 上传阶段（eager） ───────────────────────────────────────┐
│                                                             │
│ AST 管道  ──→ ①文件摘要 ──→ summary_store                   │
│     │                │                                      │
│     │                ↓                                      │
│     │          ②模块摘要 ──→ summary_store                  │
│     │                │                                      │
│     │                ↓                                      │
│     │          ┌───────────┐     doc_collector              │
│     ├─────────→│ ④模块页LLM │←──(子 README 归属)             │
│     │          └─────┬─────┘                                │
│     │                ↓                                      │
│     │          ┌───────────┐     doc_collector              │
│     └─────────→│ ⑤概览页LLM │←──(根 README + 配置            │
│                └─────┬─────┘      + 运行线索 + 统计)        │
│                      ↓                                      │
│           wiki_store:                                       │
│             - overview / module → status=generated          │
│             - file → status=pending (仅骨架)                │
└─────────────────────────────────────────────────────────────┘

┌─── 访问阶段（lazy） ────────────────────────────────────────┐
│                                                             │
│ GET /page/file_xxx                                          │
│        │                                                    │
│        ↓                                                    │
│  status=pending? ── 否 ─→ 直接返回缓存                       │
│        │                                                    │
│        是                                                    │
│        ↓                                                    │
│  ┌───────────┐                                              │
│  │③文件页 LLM│← AST + summary + call_graph + imports        │
│  └─────┬─────┘                                              │
│        ↓                                                    │
│  更新 wiki_store (status=generated) → 返回                   │
└─────────────────────────────────────────────────────────────┘
```

---

## 八、API 设计

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/wiki/generate` | 启动生成（概览页+模块页），返回 `task_id` |
| GET | `/api/wiki/status/{task_id}` | 查询生成进度 |
| GET | `/api/wiki/{project_name}` | 获取 WikiDocument 结构（index + 元数据） |
| GET | `/api/wiki/{project_name}/page/{page_id}` | 获取单页内容（若文件页 status=pending，触发即时生成） |
| POST | `/api/wiki/{project_name}/ask` | [阶段 2] 问答 |

**`GET /api/wiki/{project}/page/{page_id}` 的内部逻辑**：
```python
page = wiki_store.get_page(project, page_id)
if page.status == "pending":  # 仅文件页可能是 pending
    page = file_page_generator.generate(page_id)
    wiki_store.update_page(page, status="generated")
return page
```

---

## 九、实施顺序

1. **清理阶段**：删除旧模块 + 清理 main.py 路由
2. **调整 module_split**：把 `services/agent/skills/module_split.py` 的 system prompt 从"3-8 个"硬限改为 6.2 节的质量标准
3. **数据层**：`models/wiki_models.py` + `dao/wiki_store.py`
4. **生成器 + 数据收集**：
   - `services/wiki/doc_collector.py`（README 归属 / 配置 / 运行线索 / 项目统计）
   - `services/wiki/file_page_generator.py`
   - `services/wiki/module_page_generator.py`
   - `services/wiki/overview_generator.py`
5. **编排层**：`services/wiki_service.py`（编排流水线，含"为每个文件创建 pending 骨架"步骤）
6. **API 层**：`controllers/wiki_controller.py` + `main.py` 注册
7. **测试**：curl 跑完整闭环
8. **前端**：后端稳定后，独立开新页面（Wiki 浏览 + 问答面板）

---

## 十、后续（阶段 2 / 3）

### 阶段 2：问答
- 利用 `services/agent/` 架构
- RAG 粒度：file 级摘要 + AST 符号 + 代码切片
- 答案里的代码引用直接复用 `code_refs` 格式

### 阶段 3：自测
- 持久化用户提问历史
- 基于用户问过的页面 + 页面内容生成选择题
- 新增表：`user_questions`（project_name, page_id, question, timestamp）

---

## 十一、约束 & 注意

- 多语言 AST 已有基础（Python + Rust via `ts_parser`），Wiki 生成需语言无关
- `project_hash` 用于幂等：同一项目内容未变不重复生成
- 异步生成用 FastAPI `BackgroundTasks` 起步，若遇瓶颈再升级为 Celery/队列
