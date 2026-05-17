# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CoReader is a code understanding platform that combines AST-based static analysis, a unified LLM agent loop, wiki-style documentation generation, a multi-turn Q&A system, and a quiz feature. AST analysis supports Python, Rust, and Java; all file types are accepted for upload.

## Commands

### Backend
```bash
pip install -r backend/requirements.txt
PYTHONPATH=. uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev      # Dev server (Vite 8)
npm run build    # tsc -b && vite build
npm run lint     # ESLint
```

### Makefile
```bash
make install     # Install all deps (pip + npm)
make dev         # Backend (background) + frontend
make backend     # Backend only
make frontend    # Frontend only
```

### Environment
Copy `.env.example` to `.env`. Required vars:
- `QWEN_API_KEY` — API key
- `QWEN_BASE_URL` — OpenAI-compatible endpoint (defaults to Alibaba DashScope)
- `QWEN_MODEL` — Primary model (default: `qwen3.6-plus`)
- `QWEN_FAST_MODEL` — Lighter model for summaries (default: `MiniMax-M2.7`); empty string falls back to `QWEN_MODEL`
- `QWEN_ENABLE_THINKING` — Optional, `"true"` enables CoT for supported models (adds significant TTFT for qwen3.x)

Note: Variable names use `QWEN_` prefix but the backend speaks OpenAI-compatible protocol — any compatible gateway works. The `.env.example` includes a commented-out sub2api + MiniMax-M2.7 alternative config.

### Important: `PYTHONPATH=.`
The backend uses absolute imports (`from backend.xxx import ...`). Running uvicorn without `PYTHONPATH=.` from the project root will fail with import errors. The Makefile handles this automatically.

### Testing
No test framework is configured.

## Architecture

### Backend (FastAPI, Python)

MVC structure under `backend/`:

- **`main.py`** — Registers 4 routers (`file`, `wiki`, `qa`, `quiz`), initializes SQLite via `init_db()`
- **`controllers/`** — HTTP layer for each router
- **`services/`** — Business logic (see below)
- **`dao/`** — All persistence via `database.py` (SQLite at `backend/data/summaries.db`)
- **`models/`** — Pydantic schemas: `schemas.py`, `wiki_models.py`, `qa_models.py`, `quiz_models.py`

### Agent System (`services/agent/`)

The unified agent loop powers QA (deep mode), wiki generation, and quiz generation.

**`agent.py` — `Agent` class**

Three public interfaces:
- `run_stream(user_input, cancel_event) → AsyncGenerator[AgentEvent]` — Primary interface; yields typed events, consumed by QA and wiki services
- `run(user_input) → str` — Convenience wrapper over `run_stream()`; used by `module_service` and `SpawnAgentTool`
- `stream_run(user_input) → AsyncGenerator[str]` — Legacy pure-conversation path via `stream_qwen()`, no tools

Constructor options: `tools`, `max_iterations=30`, `token_budget` (triggers autocompaction), `compactor`, `compact_keep_last_n=4`, `auto_spawn_agent=True` (disabled for QA).

Multi-turn support: `inject_history(turns)` replays prior conversation turns including full tool chains into context before the first run.

**Main loop contract per iteration:** `ToolUseStart → ToolResult × N → IterationEnd`. Terminates with `Stop(reason, final_text)`.

**`events.py` — `AgentEvent` discriminated union**

`TextDelta | ToolUseStart | ToolResult | IterationEnd | CompactBoundary | Stop`

- `ToolUseStart(iteration, tool_id, name, args)`
- `ToolResult(iteration, tool_id, name, ok, preview, full)` — `preview` for UI, `full` for LLM
- `Stop.reason`: `completed | max_iterations | cancelled | model_error | compact_failed`

**`compactor.py` — Autocompaction**

`LLMCompactor` implements the `Compactor` protocol: calls LLM to summarize old messages into ≤800 chars when `context.estimate_tokens() > token_budget * 0.85`. Failure yields `Stop(reason="compact_failed")`.

**`context/base.py` — `Context`**

Manages OpenAI-format message history. Key methods: `add_user/add_assistant/add_tool_result`, `to_messages()`, `estimate_tokens()` (bytes/3 heuristic), `compact(summary, keep_last_n)`, `restore_turn(user_content, tool_chain, fallback_assistant)`.

**`tools/` — 7 data tools + SpawnAgentTool**

All extend `BaseTool` (`tools/base.py`) with auto-generated OpenAI function schema from type annotations:
- `GetSummariesTool` — File/module summaries by type
- `GetModulesTool` — List modules with line/symbol counts
- `GetSymbolsTool` — Symbol lookup by qualified name
- `GetCallEdgesTool` — Caller/callee relationships
- `GetFileContentTool` — File content with optional line range
- `SearchSymbolsTool` — Full-text symbol search
- `SearchCodeTool` — Full-text code search
- `SpawnAgentTool` (`spawn.py`) — Auto-injected; spawns child Agent for task decomposition (excluded from QA)

**`skills/module_split.py`** — A skill (reusable agent sub-task) for splitting wiki generation into per-module tasks.

### QA System (`services/qa/`)

**Two modes:**

- **Fast**: Single LLM call. `QAContextBuilder.build()` assembles static context (wiki outline, modules, top-8 symbols, top-20 summaries) then calls `stream_messages()`.
- **Deep**: `Agent.run_stream()` with 7 tools, `token_budget=20_000`, `max_iterations=30`. Tool events forwarded as SSE to client. After `Stop`, `final_text` is pseudo-streamed in 20-char chunks.

**Multi-turn memory:** After each deep-mode turn, full tool chain saved via `qa_store.append_message()`. On next turn, `_inject_history()` replays all prior turns via `agent.inject_history()`.

**`context_builder.py`** splits system prompt into static (`QA_SYSTEM_STATIC`) and dynamic (`QA_SYSTEM_DYNAMIC`) parts — prepared for prompt caching.

**SSE event sequence:**
```
start → token* → (tool_call → tool_result)* → (compact_boundary)? → code_refs → done
```

### Wiki System (`services/wiki/`)

**`wiki_service.py`** generates hierarchical wiki: overview → category → chapter → topic → module pages. Generation runs as a background task; status tracked in SQLite (`wiki_documents`, `wiki_pages` tables).

**`article_generator.py`** uses `Agent.run()` to generate per-page content via LLM.

### Quiz System (`services/quiz/`)

Generates quizzes from project analysis. `quiz_generator.py` creates questions; `quiz_service.py` orchestrates generation and persistence via `dao/quiz_store.py`.

### API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/file/upload` | Single file upload |
| POST | `/api/file/upload-project` | Multi-file project upload → `ProjectAST` |
| POST | `/api/wiki/generate` | Start async wiki generation → `{task_id}` |
| GET | `/api/wiki/status/{task_id}` | Poll generation status |
| GET | `/api/wiki/{project_name}` | Fetch complete `WikiDocument` |
| GET | `/api/wiki/{project_name}/export` | Download as Markdown |
| POST | `/api/qa/ask` | Stream SSE Q&A response |
| GET | `/api/qa/conversations?project_name=` | List conversations |
| GET | `/api/qa/conversations/{id}` | Get conversation + messages |
| DELETE | `/api/qa/conversations/{id}` | Delete conversation |
| POST | `/api/quiz/generate` | Start quiz generation → SSE stream |
| GET | `/api/quiz/sessions` | List quiz sessions |
| GET | `/api/quiz/{session_id}` | Get session detail |
| DELETE | `/api/quiz/{session_id}` | Delete session |
| POST | `/api/quiz/{session_id}/answer/{index}` | Submit answer |
| GET | `/api/health` | Health check (returns model name) |

### LLM Service (`services/llm/llm_service.py`)

Three interfaces over the OpenAI-compatible API:
- `call_qwen(system, user, ...)` — Non-streaming; exponential backoff (2s/5s/10s), 90s timeout
- `stream_qwen(system, user, ...)` — Streaming generator
- `call_llm(messages, tools=None, ...)` — Full tool-use support; normalizes malformed `tool_call.arguments` from upstream gateways (known issue: some gateways emit `{}{real_args}` for multi-tool-call responses)

`QWEN_FAST_MODEL` is used for summary tasks (`summary_service.py`); falls back to `QWEN_MODEL` if unset.

### AST Pipeline (`utils/analysis/`)

Multi-language support via tree-sitter (`ts_parser.py`). Languages: Python (built-in AST), Rust, Java (tree-sitter). AST-analyzable extensions defined in `config.py` (`AST_EXTENSIONS`). Pipeline: `call_graph.py` → `import_analysis.py` → `entry_detector.py` → `ProjectAST`.

Produces: `SymbolDef` (functions/classes with line ranges), `CallEdge` (call sites), entry points (routes, CLI commands, `__main__`).

### Frontend (React 19 + TypeScript, Vite 8)

Styled with Tailwind CSS. Key libraries: Zustand (state), react-markdown + remark-gfm (rendering), highlight.js (syntax), mermaid (diagrams).

**Stores (Zustand) — `store/`:**
- `useQAStore.ts` — QA conversations, streaming state, `pendingAssistant`, `compact_markers`
- `useWikiStore.ts` — Wiki document state
- `useQuizStore.ts` — Quiz state
- `useLayoutStore.ts` — Panel layout/resizing

**Component groups — `components/`:**
- `QA/` — QADrawer (resizable container), MessageBubble, ToolTimeline, ConversationMenu
- `Wiki/` — WikiLayout, WikiPageView, MarkdownRenderer
- `Quiz/` — Quiz UI
- `Layout/` — Shared layout/panel components
- `CodeDrawer/` — Source code viewer
- `Upload/` — File upload UI
- `common/` — Shared components (e.g. `Resizer.tsx`)

**Frontend QA SSE parsing** (`services/qaApi.ts`): Parses `event: ...\ndata: ...\n\n` frames, yields typed `SSEEvent` objects.

**`i18n/`** — `locales.ts` (zh/en, 80+ keys), `LanguageContext.tsx`, `ThemeContext.tsx` — React contexts, not in Zustand.

### SQLite Schema (`dao/database.py`)

Tables: `summaries`, `symbols`, `call_edges`, `modules`, `wiki_documents`, `wiki_pages`, `qa_conversations`, `qa_messages`, `project_files`. All initialized at startup via `init_db()`. DB file: `backend/data/summaries.db`.

### Key Configuration (`backend/config.py`)

- `MAX_FILE_SIZE = 1MB`, `MAX_PROJECT_SIZE = 10MB`, `MAX_PROJECT_FILES = 200`
- `SUMMARY_FUNC_LINES = 7`, `SUMMARY_TRUNCATION_PERCENT = 0.3`
- `MODULE_CODE_BUDGET_CHARS = 90_000` (wiki module page source budget)
- `MAX_WORKER_CONCURRENCY = 5` (sub2api gateway safe concurrency limit)
- `DEEP_TOKEN_BUDGET = 20_000` (defined in `services/qa/qa_service.py`, autocompaction threshold for QA deep mode)

## Data Flow

Upload → AST pipeline → SQLite (symbols, call_edges, summaries, modules) → Agent tools read from SQLite → Wiki/QA/Quiz consume agent output.

The agent tools are the only interface between the LLM and the analyzed data — they query SQLite directly. Adding new analysis capabilities means: store in SQLite via `dao/`, expose via a new tool in `services/agent/tools/`.
