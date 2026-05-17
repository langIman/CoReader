# Agent 主循环重构计划（QA 改造为消费者）

## 0 · 背景与方向

### 0.1 真实现状

我们**已经有完整的 agent 架构**：

```
backend/services/agent/
├── agent.py              # Agent 主循环（run() / stream_run()）
├── context/base.py       # Context：消息历史管理
├── memory/base.py        # Memory Protocol + ShortTermMemory
├── skills/               # Skill Protocol + module_split skill
└── tools/
    ├── base.py           # Tool Protocol + BaseTool
    ├── spawn.py          # SpawnAgentTool（≈ CC 的 Agent tool）
    └── 7 个 QA 工具      # get_summaries / get_modules / get_symbols / ...
```

它和 CC 的对应关系：

| CoReader | CC | 状态 |
|---|---|---|
| [agent.py:Agent](backend/services/agent/agent.py) `run()` 主循环 | [src/query.ts:219](../../autodl-tmp/claude-code-main/claude-code-main/src/query.ts) `query()` | ✅ 已有 |
| [Context](backend/services/agent/context/base.py) | `utils/messages.ts` normalize | ✅ 已有 |
| [Memory](backend/services/agent/memory/base.py) Protocol + ShortTermMemory | `services/SessionMemory/` | ✅ 占位接口已有 |
| [Skill](backend/services/agent/skills/base.py) Protocol + `module_split` | `~/.claude/skills/` + Skill tool | ✅ 占位接口已有 |
| [SpawnAgentTool](backend/services/agent/tools/spawn.py) | Agent tool | ✅ 已有 |
| [BaseTool](backend/services/agent/tools/base.py) + 7 工具 | tools.ts + 50+ tools | ✅ 已有 |

### 0.2 真正的问题在哪

QA 没用这套 agent 架构，原因写在 [qa_service.py:189](backend/services/qa/qa_service.py#L189) 的注释里：

> "深度模式工具循环（**不改 agent.py，这里手写一份**）"

为什么？看接口就懂——[agent.py:67](backend/services/agent/agent.py#L67) 的 `Agent.run()` 返回 `str`，吃不下 SSE 流式 yield 的需求；[agent.py:136](backend/services/agent/agent.py#L136) 的 `stream_run()` 只走 `stream_qwen` 不进主循环，没 tool-use。

QA 又必须流式吐 `tool_call` / `tool_result` / `budget_exhausted` / `token` 事件给前端，于是另起炉灶手写了一份 ——**这就是当前的真问题，agent 架构和 QA 是割裂的**。

### 0.3 改造方向（一句话）

**把 [agent.py](backend/services/agent/agent.py) 升级为事件流主循环，QA 退化为消费者，[_deep_stream](backend/services/qa/qa_service.py#L186) 整段删除**。

之后任何要用主循环的场景（QA、Wiki 生成、Topic 调优、未来的页面生成）都消费同一份 `Agent.run_stream()`，CC 的形状是这样，我们也照办。

---

## 1 · CC 主循环架构要点（搬什么）

> 定位以 [autodl-tmp/claude-code-main/claude-code-main](../../autodl-tmp/claude-code-main/claude-code-main/) 为根。

| 要点 | CC 位置 | 我们怎么搬 |
|---|---|---|
| `query()` async generator | [src/query.ts:219](../../autodl-tmp/claude-code-main/claude-code-main/src/query.ts) | `Agent.run_stream()` 改成 async generator，yield `AgentEvent` |
| `queryLoop` 状态机 | [src/query.ts:241](../../autodl-tmp/claude-code-main/claude-code-main/src/query.ts) | `Agent._loop()`，循环体提取 |
| Stop reason 枚举 | [src/query.ts:1340](../../autodl-tmp/claude-code-main/claude-code-main/src/query.ts) | `StopReason` Literal，Agent 在每个出口 yield `Stop(reason=...)` |
| `runTools` 调度（CC 是并行，我们先串行） | `src/services/tools/toolOrchestration.ts` | 沿用 [agent.py](backend/services/agent/agent.py) 现有的 for 循环串行；并行属优化（§7） |
| Autocompact | [src/query.ts:376-430](../../autodl-tmp/claude-code-main/claude-code-main/src/query.ts) | `Context.compact()` + `Agent` 在循环里调度 |
| System prompt 静态/动态分块 | `src/constants/prompts.ts` + `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` | `Agent.__init__(system_static, system_dynamic)` 接口拆好——Qwen 缓存留作优化 |

**不搬产品形态**：Hook、MCP、权限提示（canUseTool）、Tombstone、多模态。
**不搬性能/调度优化**（架构稳后再做，详见 §7）：工具并行、流式工具执行、prompt cache_control、精确 tokenizer。

---

## 2 · 目标架构

### 2.1 升级后的目录

```
backend/services/agent/                    # ⭐ 唯一主循环所在地
├── agent.py                                # Agent: run_stream() / run() / stream_run()
├── events.py                               # 🆕 AgentEvent discriminated union
├── compactor.py                            # 🆕 autocompact 工具函数
├── context/base.py                         # ⚙️ 加 estimate_tokens() + compact()
├── memory/base.py                          # 不动
├── skills/                                 # 不动
└── tools/
    ├── base.py                             # 不动
    ├── spawn.py                            # 不动（子 Agent 仍调 run() 取 str）
    └── 7 个 QA 工具                        # 不动

backend/services/qa/
├── qa_service.py                           # ✂️ 删 _deep_stream；deep 改成 Agent.run_stream() + 事件翻译
├── context_builder.py                      # ⚙️ 不再造 ctx 自己的循环态；只产 system_static / system_dynamic / first_user
├── code_refs.py                            # 不动
└── retrieval.py                            # 不动

backend/services/llm/prompts/
└── qa_prompts.py                           # ⚙️ 拆 STATIC（工具说明+输出规则）/ DYNAMIC（项目元信息）
```

### 2.2 升级后的 Agent 接口

> **设计原则**：`__init__` 签名零变更——不破坏现有调用方 [module_service.py:37](backend/services/module_service.py#L37) 和 [SpawnAgentTool:68](backend/services/agent/tools/spawn.py#L68)。"static/dynamic 拆分"放在 QAContextBuilder 层，QA 自己拼好整段 `system_prompt` 传进来。这跟 CC 一致——`fetchSystemPromptParts()` 在外层组装，`query()` 接拼好的字符串。

```python
# backend/services/agent/agent.py
from backend.services.agent.events import AgentEvent, StopReason

class Agent:
    def __init__(
        self,
        system_prompt: str,                  # ⚠️ 签名不变，向后兼容 module_service / spawn
        tools: list[BaseTool | Tool] | None = None,
        max_iterations: int = 10,
        token_budget: int | None = 20_000,   # 🆕 None = 不限；触发后走 compactor
        enable_thinking: bool | None = None,
        compactor: Compactor | None = None,  # 🆕 可注入；None 则不做 compaction
    ) -> None: ...

    async def run_stream(
        self,
        user_input: str,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """主循环 + 事件流。所有消费者（QA/Wiki/Topic）走这个接口。"""

    async def run(self, user_input: str) -> str:
        """便捷接口：消费 run_stream 取最终文本。供 batch 场景。
        ⚠️ 必须与重构前 run() 行为字节级等价——module_service 和 spawn 依赖此。
        """
        final = ""
        async for ev in self.run_stream(user_input):
            if ev.type == "stop":
                final = ev.final_text
        return final

    async def stream_run(self, user_input: str) -> AsyncGenerator[str, None]:
        """⚠️ 旧的纯对话接口（grep 显示无人调用），保留以防外部依赖；新代码用 run_stream"""
```

### 2.3 AgentEvent（discriminated union）

```python
# backend/services/agent/events.py
from typing import Literal, Annotated, Union
from pydantic import BaseModel, Field

class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    delta: str

class ToolUseStart(BaseModel):
    type: Literal["tool_use_start"] = "tool_use_start"
    iteration: int
    tool_id: str
    name: str
    args: dict

class ToolResult(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    iteration: int
    tool_id: str
    name: str
    ok: bool
    preview: str          # 给前端的截断版（≤500 字符）
    full: str             # 喂回 LLM 的完整版（不发前端）

class IterationEnd(BaseModel):
    type: Literal["iteration_end"] = "iteration_end"
    iteration: int
    input_tokens: int
    output_tokens: int

class CompactBoundary(BaseModel):
    type: Literal["compact_boundary"] = "compact_boundary"
    summarized_turns: int
    new_input_tokens: int

class Stop(BaseModel):
    type: Literal["stop"] = "stop"
    reason: Literal["completed", "max_iterations", "cancelled", "model_error", "compact_failed"]
    final_text: str

AgentEvent = Annotated[
    Union[TextDelta, ToolUseStart, ToolResult, IterationEnd, CompactBoundary, Stop],
    Field(discriminator="type"),
]
```

### 2.4 QA 改造后形状

```python
# backend/services/qa/qa_service.py（深度模式部分，伪代码）
async def _deep_stream(req: QARequest):
    sys_static, sys_dynamic, first_user = QAContextBuilder(req).build_for_agent()
    agent = Agent(
        system_static=sys_static,
        system_dynamic=sys_dynamic,
        tools=_build_deep_tools(),
        max_iterations=8,
        token_budget=20_000,
    )

    full_text_buf: list[str] = []
    tool_events: list[dict] = []

    async for ev in agent.run_stream(first_user):
        match ev.type:
            case "text_delta":
                full_text_buf.append(ev.delta)
                yield ("token", {"delta": ev.delta})
            case "tool_use_start":
                yield ("tool_call", {"iteration": ev.iteration, "name": ev.name,
                                     "args_preview": ev.args})
            case "tool_result":
                tool_events.append({...})
                yield ("tool_result", {"iteration": ev.iteration, "name": ev.name,
                                       "ok": ev.ok, "preview": ev.preview})
            case "compact_boundary":
                yield ("compact_boundary", {"summarized_turns": ev.summarized_turns})
            case "stop":
                yield ("__final__", {"content": "".join(full_text_buf),
                                     "tool_events": tool_events,
                                     "stop_reason": ev.reason})
                return
```

---

## 3 · 分阶段执行

> 每阶段独立可测，跑通再合下一阶段。每阶段一个独立 commit，便于回滚。

### 阶段 1 · agent.py 加事件流主循环（不改其他）

**目标**：[agent.py](backend/services/agent/agent.py) 多一条 `run_stream()` 通路，`run()` 改成基于它的薄包装；旧行为完全兼容。

- [ ] 新建 [backend/services/agent/events.py](backend/services/agent/events.py)，定义 `AgentEvent` union + `StopReason`
- [ ] 改 [agent.py](backend/services/agent/agent.py)：
  - 把 `run()` 主循环逻辑挪进新 `_loop_stream()`，yield `AgentEvent`
  - 提供 `run_stream(user_input, cancel_event=None)` 公共接口
  - 旧 `run()` 改为：`async for ev in self.run_stream(...): if ev.type == "stop": return ev.final_text`
  - 暂不改 `__init__` 签名（system_static / token_budget 等下个阶段加）
- [ ] 单测：fake LLM 客户端跑通三种场景
  - 纯文本立即结束 → `Stop(reason="completed")`
  - 一轮工具 + 文本结束 → 事件序列 `ToolUseStart → ToolResult → TextDelta* → Stop`
  - 撞 `max_iterations` → `Stop(reason="max_iterations")`
- [ ] **等价性单测**（关键）：fake LLM 跑通 module_split 路径，对比重构前后 `agent.run()` 返回的字符串字节相等。包括：
  - 首次返回合法 JSON 的路径
  - 首次非 JSON、第二次 `_JSON_RETRY_PROMPT` 后才合法的路径
  - 撞 max_iterations 兜底文本路径（确保兜底文本不变："`[Agent 达到最大迭代次数]`" 或 LLM 最后给的 content）

**验收**：现有任何调 `Agent.run()` 的代码不受影响（[module_service](backend/services/module_service.py)、[SpawnAgentTool](backend/services/agent/tools/spawn.py)）。

---

### 阶段 2 · QA 接入 Agent.run_stream，删掉 _deep_stream

**目标**：QA 深度模式不再有自己的循环，[_deep_stream](backend/services/qa/qa_service.py#L186) 整段移除。

- [ ] [qa_service.py](backend/services/qa/qa_service.py) `_deep_stream` 改写为消费 `Agent.run_stream()` 的事件翻译层（见 §2.4 伪代码）
- [ ] [context_builder.py](backend/services/qa/context_builder.py) 增加 `build_for_agent()` 返回 `(system_static, system_dynamic, first_user)` 三元组；旧 `build()` 保留给 fast 模式
- [ ] 删除 [qa_service.py](backend/services/qa/qa_service.py) 里的 `_estimate_tokens` / `DEEP_TOKEN_BUDGET` / `over_budget` 逻辑（迁移给 Agent，下阶段做）
- [ ] 暂保留 `MAX_ITER_DEEP` 但改成传给 `Agent(max_iterations=...)`
- [ ] 暂保留 `_pseudo_stream` 兜底（Agent 流式失败时的降级）
- [ ] 回归：黄金问答样例的 SSE 流和重构前应字节级一致（除时间戳）

**验收**：`grep "_deep_stream" backend/` 在删除后只剩函数已不再存在的状态；前端无感知。

---

### 阶段 3 · System prompt 静态/动态拆分（在 QAContextBuilder 层完成）

**目标**：把 CC 的 `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 边界搬过来——**只在 prompt 装配层拆，Agent 接口零变更**。

- [ ] [qa_prompts.py](backend/services/llm/prompts/qa_prompts.py) 把 `QA_SYSTEM_PROMPT_DEEP` 拆成两个模板常量：
  - `QA_SYSTEM_STATIC`：工具描述、输出规则、`#wiki:` / `#code:` 语法、code_refs 格式（**项目无关、轮次稳定**）
  - `QA_SYSTEM_DYNAMIC_TEMPLATE`：项目名、Wiki outline、模块列表的填充模板（**项目相关、可能轮次更新**）
- [ ] [context_builder.py](backend/services/qa/context_builder.py) `_build_deep` 改成返回 `(system_prompt, first_user)` 二元组：
  - 内部把两段拼成完整 system_prompt（`STATIC + "\n\n" + DYNAMIC.format(...)`）
  - QA 调 `Agent(system_prompt=full_text, ...)`，Agent 接口完全不动
- [ ] **不动**：[Agent.__init__](backend/services/agent/agent.py)、[Context](backend/services/agent/context/base.py)、[module_service.py](backend/services/module_service.py)、[spawn.py](backend/services/agent/tools/spawn.py)

**验收**：拆分后行为字节级等价（拼起来的 system_prompt 应与原 `QA_SYSTEM_PROMPT_DEEP` 等价或仅排版差异）。

> 不在本期做：Qwen `cache_control: ephemeral` multipart、`cached_tokens` 命中率统计、把 multipart 暴露到 Agent 接口——挪到 §7 优化集。届时再让 Agent 接 `system_prompt: str | list[dict]` 多态参数即可，拆分的字符串已就位、改动局限在 LLM 拼装层。

---

### 阶段 4 · Compactor 接口 + CompactBoundary 事件

**目标**：搬 CC 的 autocompact **架构位置**——loop 在每轮开头有"判定 + compact + yield boundary"的钩子。**搬位置不搬精度**：summarize 实现允许是简陋版。

- [ ] [context/base.py](backend/services/agent/context/base.py) 加方法：
  - `estimate_tokens() -> int`（字符数 / 3 粗估即可，**不接精确 tokenizer**）
  - `compact(summary: str, keep_last_n: int)`：用 `summary` 替换 `_messages[:-keep_last_n]`
- [ ] 新建 [backend/services/agent/compactor.py](backend/services/agent/compactor.py) 定义 Protocol + 默认实现：
  ```python
  class Compactor(Protocol):
      async def summarize(self, messages: list[dict]) -> str: ...

  class LLMCompactor:
      """默认：单次轻量 Qwen 调用做摘要。Prompt 简陋版即可，调优在 §8。"""
      async def summarize(self, messages: list[dict]) -> str: ...
  ```
- [ ] `Agent._loop_stream()` 在每轮开头：
  - `if context.estimate_tokens() > token_budget * 0.85:`
  - 调 `compactor.summarize()`，`context.compact(...)`
  - yield `CompactBoundary` 事件
- [ ] 删除 QA 侧旧的 `budget_exhausted` 关 tools 逻辑

**验收**：触发一次 compact → 看到 `CompactBoundary` 事件、循环继续推进。**不验收摘要质量**——质量调优是优化，不是架构。

> 不在本期做：精确 tokenizer、摘要 prompt 多次调优、保留某些消息的 priority 策略——挪到 §8。

---

### 阶段 5 · 结构化 Stop reason

**目标**：前端能精确显示"为什么停了"。

- [ ] `StopReason` 枚举落地（已在 events.py）
- [ ] `Agent.run_stream()` 在每个出口 yield `Stop(reason=..., final_text=...)`，再 return
- [ ] [qa_controller.py](backend/controllers/qa_controller.py) SSE `done` payload 加 `stop_reason` 字段
- [ ] [qa_store.py](backend/dao/qa_store.py) `qa_messages` 表加 `stop_reason TEXT NULL`（迁移脚本：`ALTER TABLE`）

**验收**：用 AbortController 取消 → "已取消"；构造死循环 → "达到上限"；触发 compact → "compact_failed" 时显示降级信息。

---

### 阶段 6 · 前端事件升级（小改）

**目标**：把后端新增的事件展示出来。

- [ ] [qaApi.ts](frontend/src/services/qaApi.ts) `SSEEvent` union 加 `iteration_end | compact_boundary`
- [ ] [useQAStore.ts](frontend/src/store/useQAStore.ts) `ask()` 处理这两种事件 + `stop_reason`
- [ ] [ToolTimeline.tsx](frontend/src/components/QA/ToolTimeline.tsx) 渲染：
  - iteration 分隔符（"—— 第 3 轮 ——"）
  - compact 标记（"📦 已压缩 N 轮历史"）
- [ ] [MessageBubble.tsx](frontend/src/components/QA/MessageBubble.tsx) 显示 `stopReason`（仅非 `completed` 时）
- [ ] `npm run lint && npm run build` 通过

**验收**：完整深度问答看到"轮次分隔 + 压缩标记 + 停止原因"。

---

### 阶段 7 · 其他消费者迁移（远期，非本期必做）

**目标**：Wiki 生成 / Topic 调优也改为消费 `Agent.run_stream()`。

- [ ] 评估 Wiki 模块页生成是否值得迁移（当前是两步管线，可能更适合保留）
- [ ] Topic 调优 step1 / step2 评估
- [ ] 这一步等 QA 在生产稳定一段时间再启动

---

## 4 · 待你拍板的决策

1. **fast 模式是否一起重构进 Agent？**
   - 推荐：**不动**。fast 是单次 LLM 调用 + 静态上下文，不需要 loop，进 Agent 反而绕
2. **`max_iterations` 默认值**
   - 当前 `MAX_ITER_DEEP=8`，CC 没硬上限（靠 hooks）。建议保留 8，autocompact 接住"快撞线"
3. **Compactor 的 `keep_last_n`**
   - 建议 4：保留最近 2 轮（user + assistant + tool_results），平衡连续性和压缩率
4. **SpawnAgentTool 是否要在阶段 1 一起加 run_stream 透传**
   - 推荐：**先不动**。子 agent 现在调 `child.run() -> str` 就够（父 agent 把子结果当工具返回值）；要透传子事件流是远期阶段 7 的事

---

## 5 · 风险与回滚

### 5.1 既有调用方影响清单

| 调用方 | 调用 | 阶段 | 风险 | 应对 |
|---|---|---|---|---|
| [module_service.py:37](backend/services/module_service.py#L37) | `Agent(system_prompt=...).run()` × 2（首问 + JSON 重试） | 阶段 1 | ⚠️ 高 | 单测覆盖：fake LLM 跑现有 module_split 黄金路径，重构前后 raw 字符串字节相等 |
| [SpawnAgentTool:68](backend/services/agent/tools/spawn.py#L68) | `Agent(system_prompt=..., max_iterations=5, enable_thinking=...).run()` | 阶段 1 | ⚠️ 高 | 同上单测；保证 `enable_thinking` 透传链路不变 |
| [qa/context_builder.py:48](backend/services/qa/context_builder.py#L48) | `Context(QA_SYSTEM_PROMPT_FAST)` | 阶段 4 仅新增方法 | ✅ 低 | 不破坏现有 add_user / to_messages |
| [qa_service:198](backend/services/qa/qa_service.py#L198) deep 路径 | `_deep_stream(req)` | 阶段 2 | ⚠️ 中 | 必须**单 commit** 切换；中间状态不能存活 |
| `MAX_ITER_DEEP` 共享常量 | qa_service + context_builder（嵌进 system_prompt 提示 LLM） | 阶段 2 | ✅ 低 | 保留为 QA 配置常量，作为 `Agent(max_iterations=...)` 的传入值 |
| `qa_messages` 加 `stop_reason` 列 | 老库迁移 | 阶段 5 | ✅ 低 | 仿 [database.py:60 call_edges 迁移](backend/dao/database.py#L60)：PRAGMA table_info + ALTER TABLE ADD COLUMN |
| `stream_run()` | grep 显示**无人调** | — | ✅ 零 | 保留以防外部依赖 |

### 5.2 通用风险

| 风险 | 应对 |
|---|---|
| 阶段 2 QA 重构期间事件序列漂移 | dump 一组黄金 SSE 流做快照测试，CI 比对 |
| 阶段 4 Compactor 摘要劣质导致答非所问 | **本期不解决**——摘要质量是优化（§7），骨架阶段允许劣质；用户可"新建会话"逃生 |
| `cancel_event` 在 LLM 流式中插入打断不及时 | 每个 chunk 之间检查 `cancel_event.is_set()`，及时 `break` 并 yield `Stop(reason="cancelled")` |

回滚：每阶段独立 commit，出问题 `git revert` 单段。

---

## 6 · 不在本期范围（CC 有但我们不搬）

| 不做 | 理由 |
|---|---|
| Hook 系统（CC 的 stop/pre/post hooks） | 后端自治，没"用户注入自动化"的需求 |
| MCP 集成 | 产品方向不同 |
| 权限提示（canUseTool / AskUserQuestion） | 后端自信任 |
| Tombstone / withheld message | 我们 compact 在轮间做，不会出现"流到一半撤回" |
| 多模态（图片输入） | 当前 QA 只接文本 |
| 跨会话 memory | Memory Protocol 已占位，等需求出现再实现 |
| Skill tool 装载到 QA | Skill 协议已有，但 QA 当前不需要 |
| Sub-Agent 事件流透传到父 | SpawnAgentTool 已有，子 agent 调 `run()` 取 str 就够 |

---

## 7 · 不在本期范围（架构已有，纯优化挪后）

> 这些都是 CC 里有、我们也想要、**但属于"在已有形状里做更好的事"**——架构骨架完成后单独立项。

| 优化项 | 位于哪个架构面之上 | 后续动什么 |
|---|---|---|
| Qwen prompt caching（`cache_control: ephemeral` multipart） | 阶段 3（system prompt 已拆好） | 改 [Context.to_messages()](backend/services/agent/context/base.py) 拼装方式 + [llm_service.py](backend/services/llm/llm_service.py) 兼容 multipart |
| `cached_tokens` 命中率统计与日志 | 阶段 3 | 在 LLM response 里读 `prompt_cache_hit_tokens` 打点 |
| 工具并行调度（`asyncio.as_completed`） | 阶段 1 主循环 | `Agent._loop_stream()` 里 dispatch 改并行 |
| 流式工具执行（边出 tool_use 边跑） | 阶段 1 主循环 | 取决于 Qwen SSE 是否给 partial tool_calls，先做侦察 |
| 精确 tokenizer 替代字符数估算 | 阶段 4 Compactor | 接入 `tiktoken` 或 Qwen tokenizer |
| Compactor 摘要质量调优 | 阶段 4 Compactor | summarize prompt 多次实验 + priority 策略保留关键消息 |
| 工具结果智能截断（按重要性截断而非长度） | 阶段 1 工具调度 | `_truncate` 加结构感知 |

**判定原则**：上面任意一项做不做都不影响 QA 的"形状"——agent.py 是核心、QA 是消费者、AgentEvent 是事件协议。**架构稳了，这些是迭代议题**。

---

## 8 · 完成标准

骨架级验收，不验收性能/质量：

- [ ] [agent.py](backend/services/agent/agent.py) 是**唯一**的主循环实现，QA 不再有循环逻辑
- [ ] [_deep_stream](backend/services/qa/qa_service.py) 已删除
- [ ] `Agent.__init__` 接受 `system_static` + `system_dynamic` 两参数
- [ ] `Compactor` Protocol + `LLMCompactor` 默认实现已就位
- [ ] 触发一次 compact 能看到 `CompactBoundary` 事件、循环正常推进（不验收摘要质量）
- [ ] 前端展示 stop reason 和 iteration 分隔符
- [ ] 单测：fake LLM 客户端跑通 `Agent.run_stream()` 五种 stop reason 分支（completed / max_iterations / cancelled / model_error / compact_failed）
- [ ] [CLAUDE.md](CLAUDE.md) 架构章节更新："agent 主循环 + QA 消费者"
