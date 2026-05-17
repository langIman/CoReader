# Eval 框架计划

## Context

CoReader 三个核心功能（Wiki 文档生成、QA 问答、Quiz 出题）缺乏质量评测手段。目标：建立一套可自动运行的评测框架，产出可量化的硬指标，用于迭代调优和简历展示。本阶段只做可程序化验证的指标，不引入 LLM-as-Judge。

## 目录结构

```
eval/
├── README.md              # 说明文档：指标定义、运行方式、输出格式
├── eval_wiki.py           # Wiki 文档评测
├── eval_qa.py             # QA 问答评测
├── eval_quiz.py           # Quiz 出题评测
├── qa_golden.json         # QA 金标问题集（手工维护）
├── report/                # 评测输出目录（gitignore）
│   └── .gitkeep
└── utils.py               # 共享工具（DB 连接、AST 查询、输出格式化）
```

## 评测指标设计

### 1. Wiki 文档评测 (`eval_wiki.py`)

**输入**: 已生成的 WikiDocument（从 SQLite 读取）

| 指标 | 方法 | 输出 |
|------|------|------|
| 覆盖率 | wiki pages 引用的模块 vs DB 中全部 modules | covered/total 比例 |
| 实体准确率 | content_md 中提到的函数名/类名 → grep symbols 表验证是否存在 | precision % |
| 代码引用有效性 | code_refs 中的 file+line_range → 读实际文件验证行存在且非空 | valid/total |
| 内部链接完整性 | outgoing_links (#wiki:page_id) → 验证目标 page_id 存在 | broken links 数 |
| 内容非空率 | 非 category 页面 content_md 不为 None 且 len > 100 | ratio |

**运行**: `python eval/eval_wiki.py --project <project_name>`  
**输出**: JSON report → `eval/report/wiki_<project>_<timestamp>.json`

### 2. QA 问答评测 (`eval_qa.py`)

**输入**: `qa_golden.json` 中定义的问答对 + 实际调用 QA API 获取回答

```json
// qa_golden.json 格式
[
  {
    "question": "项目的入口文件是哪个？",
    "expected_keywords": ["main.py", "uvicorn"],
    "expected_code_refs": ["backend/main.py"],
    "mode": "deep"
  }
]
```

| 指标 | 方法 | 输出 |
|------|------|------|
| 关键词命中率 | 回答 content 中是否包含 expected_keywords | hit/total per question |
| 代码引用准确率 | 返回的 code_refs 中 file 是否在 expected_code_refs 内 | precision/recall |
| 完成率 | stop_reason == "completed" 的比例 | ratio |
| 工具使用合理性 | deep 模式下 tool_events 不为空（证明确实查了代码） | ratio |

**运行**: `python eval/eval_qa.py --project <project_name>`（需要后端运行中，会实际调 `/api/qa/ask`）  
**输出**: JSON report → `eval/report/qa_<project>_<timestamp>.json`

### 3. Quiz 出题评测 (`eval_quiz.py`)

**输入**: 已生成的 QuizSession（从 SQLite 读取）+ 项目 AST 数据

| 指标 | 方法 | 输出 |
|------|------|------|
| 答案实体可验证率 | 正确选项 text 中的函数名/类名/文件名 → grep symbols/summaries 表 | ratio |
| 题目多样性 | 10 题的 code_ref 覆盖不同文件数 | unique_files / 10 |
| 选项格式合规率 | 4 个选项非空、correct_key 合法、explanation 非空 | ratio |
| 自洽性（核心）| 把 question + 源码喂给同一模型做题，验证能否选对 | accuracy % |

注：自洽性测试需要调用 LLM，但这不是 LLM-as-Judge（不是让 LLM 打分），而是功能性验证——题目是否有唯一正确答案。用 `call_qwen` 即可。

**运行**: `python eval/eval_quiz.py --project <project_name> [--session <session_id>]`  
**输出**: JSON report → `eval/report/quiz_<project>_<timestamp>.json`

## 共享工具 (`utils.py`)

- `get_db_connection()` — 复用 `backend/dao/database.py` 的 SQLite 连接
- `load_symbols(project_name)` — 从 symbols 表拉全量符号名用于验证
- `load_summaries(project_name)` — 拉文件/模块摘要
- `check_file_lines(file_path, start, end)` — 验证文件行是否存在
- `print_report(data, output_path)` — 统一 JSON 输出 + 终端 summary 打印

## 实现顺序

1. `eval/utils.py` — 共享基础设施
2. `eval/eval_wiki.py` — 最简单，纯读 DB + 验证，不需要调 API
3. `eval/eval_quiz.py` — 第二步，大部分也是读 DB，自洽性测试需调一次 LLM
4. `eval/eval_qa.py` + `eval/qa_golden.json` — 最后，需要手工编写 golden set + 调用 API
5. `eval/README.md` — 最后写文档

## 关键文件依赖

- `backend/dao/database.py` — DB 路径和连接方式
- `backend/dao/wiki_store.py` — `load_wiki_document()`
- `backend/dao/quiz_store.py` — `get_session_detail()`
- `backend/dao/qa_store.py` — `get_user_questions()`
- `backend/models/wiki_models.py` — WikiDocument/WikiPage schema
- `backend/models/quiz_models.py` — QuizQuestion/QuizSession schema
- `backend/services/llm/llm_service.py` — `call_qwen()` (quiz 自洽性测试复用)

## 验证方式

1. 先用当前 DB 中已有的 wiki/quiz 数据跑 `eval_wiki.py` 和 `eval_quiz.py`，确认能产出 JSON report
2. 编写 5 条 golden QA 问题，启动后端后跑 `eval_qa.py`
3. 最终各脚本输出格式统一，终端打印 one-liner summary（如 `Wiki eval: entity_accuracy=92%, coverage=85%, broken_links=0`）
