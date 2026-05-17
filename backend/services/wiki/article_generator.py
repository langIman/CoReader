"""核心架构章节 & 专题深入的页面生成器。

两者的上下文和输出格式高度相似——输入一个 spec（title + brief），
外加全局的项目元数据，生成一篇 Markdown 文章。
共用 _generate_article 走同一套后处理管线。

专题页走两步管线（playbook §六 方案 B）：
  step1 侦察 → step2 写作。详见 generate_topic_page。
"""

from __future__ import annotations

import logging

from backend.models.graph_models import ProjectAST, SymbolDef
from backend.models.wiki_models import PageMetadata, WikiPage
from backend.services.llm.llm_service import call_qwen
from backend.services.llm.prompts.wiki_prompts import (
    build_chapter_page_prompt,
    build_topic_page_prompt,
    build_topic_recon_prompt,
)
from backend.services.wiki._postprocess import (
    extract_outgoing_links,
    parse_llm_page_output,
    resolve_code_refs,
)
from backend.services.wiki.doc_collector import DocBundle
from backend.services.wiki.outliner import ChapterSpec, TopicSpec
from backend.services.wiki.page_ids import chapter_id, module_id, topic_id
from backend.utils.data_format import parse_llm_json

logger = logging.getLogger(__name__)


async def generate_chapter_page(
    *,
    index: int,
    spec: ChapterSpec,
    project_name: str,
    project_summary: str | None,
    modules: list[dict],
    module_summaries: list[str],
    project_files: dict[str, str],
    ast_model: ProjectAST,
    doc_bundle: DocBundle,
    allowed_page_ids: list[str],
) -> WikiPage:
    system, user = build_chapter_page_prompt(
        chapter_title=spec.title,
        chapter_brief=spec.brief,
        project_name=project_name,
        project_summary=project_summary,
        modules_text=_fmt_modules(modules, module_summaries),
        module_deps_text=_fmt_module_deps(ast_model, modules),
        module_skeletons_text=_fmt_module_skeletons(modules, ast_model),
        key_files_source_text=_fmt_key_files_source(modules, ast_model, project_files),
        run_hints_text=_fmt_run_hints(doc_bundle),
        allowed_page_ids=allowed_page_ids,
        min_code_refs=4,
    )
    return await _generate_article(
        page_id=chapter_id(index),
        page_type="chapter",
        title=spec.title,
        brief=spec.brief,
        system=system,
        user=user,
        ast_model=ast_model,
    )


async def generate_topic_page(
    *,
    index: int,
    spec: TopicSpec,
    project_name: str,
    project_summary: str | None,
    modules: list[dict],
    module_summaries: list[str],
    project_files: dict[str, str],
    ast_model: ProjectAST,
    allowed_page_ids: list[str],
) -> WikiPage:
    """专题页两步管线（playbook §六 方案 B）：

    Step 1 侦察：从函数级符号目录里挑 3-8 个最相关符号（thinking off，极简 prompt）
    Step 2 写作：拿 step1 选中符号的源码片段写文章

    解决 baseline 的核心病：单 shot 凭模块摘要硬编"教科书"内容。
    """
    selected = await _topic_recon(
        spec=spec,
        modules=modules,
        ast_model=ast_model,
        project_files=project_files,
    )

    system, user = build_topic_page_prompt(
        topic_title=spec.title,
        topic_brief=spec.brief,
        project_name=project_name,
        project_summary=project_summary,
        modules_text=_fmt_modules(modules, module_summaries),
        module_deps_text=_fmt_module_deps(ast_model, modules),
        relevant_symbols_text=_fmt_relevant_symbols(selected, ast_model, project_files),
        focused_source_text=_fmt_focused_symbols_source(
            selected, ast_model, project_files,
        ),
        allowed_page_ids=allowed_page_ids,
        min_code_refs=3,
    )
    return await _generate_article(
        page_id=topic_id(index),
        page_type="topic",
        title=spec.title,
        brief=spec.brief,
        system=system,
        user=user,
        ast_model=ast_model,
    )


async def _topic_recon(
    *,
    spec: TopicSpec,
    modules: list[dict],
    ast_model: ProjectAST,
    project_files: dict[str, str],
) -> list[dict]:
    """Step1：让 LLM 从符号目录里挑 3-8 个相关符号。

    返回 [{qualified_name, reason}, ...]；JSON 解析失败或 LLM 返回为空时返回 [],
    上游会以"无可读源码"提示填入 step2——退化为更接近 baseline 的行为，但仍可生成页面。
    """
    system, user = build_topic_recon_prompt(
        topic_title=spec.title,
        topic_brief=spec.brief,
        modules_text=_fmt_modules(modules, [m.get("description", "") for m in modules]),
        symbol_catalog_text=_fmt_symbol_catalog_for_recon(
            modules, ast_model, project_files,
        ),
    )
    raw = await call_qwen(system, user, enable_thinking=False, per_attempt_timeout=150.0)
    try:
        data = parse_llm_json(raw)
    except Exception as e:
        logger.warning("Topic recon JSON parse failed: %s; raw head: %r", e, raw[:200])
        return []
    selected = data.get("relevant_symbols") or []
    if not isinstance(selected, list):
        return []
    return [s for s in selected if isinstance(s, dict) and s.get("qualified_name")]


# --------- 共用调用 ---------


async def _generate_article(
    *,
    page_id: str,
    page_type: str,
    title: str,
    brief: str,
    system: str,
    user: str,
    ast_model: ProjectAST,
) -> WikiPage:
    raw = await call_qwen(system, user, enable_thinking=False, per_attempt_timeout=150.0)
    content_md, raw_refs = parse_llm_page_output(raw)
    code_refs = resolve_code_refs(raw_refs, ast_model)
    outgoing = extract_outgoing_links(content_md)

    return WikiPage(
        id=page_id,
        type=page_type,  # type: ignore[arg-type]
        title=title,
        path=None,
        status="generated",
        content_md=content_md,
        metadata=PageMetadata(
            outgoing_links=outgoing,
            code_refs=code_refs,
            brief=brief or None,
        ),
    )


# --------- 共用文本格式化 ---------


def _fmt_modules(modules: list[dict], summaries: list[str]) -> str:
    if not modules:
        return "（无模块）"
    lines = []
    for idx, m in enumerate(modules):
        s = (summaries[idx] if idx < len(summaries) else "").strip() or m.get("description", "")
        lines.append(f"- `{module_id(idx)}` **{m['name']}** — {s or '（暂无摘要）'}")
    return "\n".join(lines)


def _fmt_module_deps(ast_model: ProjectAST, modules: list[dict]) -> str:
    path_to_idx: dict[str, int] = {}
    for i, m in enumerate(modules):
        for p in m.get("paths") or []:
            path_to_idx[p] = i

    pair_counts: dict[tuple[int, int], int] = {}
    for e in ast_model.edges:
        if not e.callee_resolved or e.callee_resolved not in ast_model.definitions:
            continue
        dst_file = ast_model.definitions[e.callee_resolved].file
        src_m = path_to_idx.get(e.file)
        dst_m = path_to_idx.get(dst_file)
        if src_m is None or dst_m is None or src_m == dst_m:
            continue
        pair_counts[(src_m, dst_m)] = pair_counts.get((src_m, dst_m), 0) + 1

    if not pair_counts:
        return "（无跨模块调用）"
    lines = []
    for (src, dst), cnt in sorted(pair_counts.items(), key=lambda x: -x[1])[:30]:
        lines.append(f"- `{module_id(src)}` → `{module_id(dst)}` ({cnt} 处)")
    return "\n".join(lines)


def _fmt_key_files_source(
    modules: list[dict],
    ast_model: ProjectAST,
    project_files: dict[str, str],
    budget_chars: int = 30000,
) -> str:
    """按"每模块最 hot 文件 + 全局 hot score"挑出关键文件并附完整源码。

    iter1 用 skeleton（仅符号清单）暴露了一个硬伤：LLM 看不到注释代码块和
    inner class 方法，导致幻觉演进版本（如把 v3.5 BlockingQueue 当成现役 v4）。
    iter2 直接把关键文件源码塞进 prompt，让 LLM 能读到 4 代注释代码。

    策略：
    - 按 (in+out) 度数算每文件 hot score
    - 每模块取 hot 最高的 1 个文件，确保 chapter 能看到所有模块的代表
    - 按全局 hot 从高到低排序填充——最 hot 的不截断（如 VoucherOrderServiceImpl 含 4 代演进）
    - 超出预算的尾部文件截断或丢弃
    """
    in_deg: dict[str, int] = {}
    out_deg: dict[str, int] = {}
    for e in ast_model.edges:
        if e.callee_resolved:
            in_deg[e.callee_resolved] = in_deg.get(e.callee_resolved, 0) + 1
        out_deg[e.caller] = out_deg.get(e.caller, 0) + 1

    file_score: dict[str, int] = {}
    for s in ast_model.definitions.values():
        sc = in_deg.get(s.qualified_name, 0) + out_deg.get(s.qualified_name, 0)
        file_score[s.file] = file_score.get(s.file, 0) + sc

    chosen: list[str] = []
    for m in modules:
        paths = m.get("paths") or []
        ranked = sorted(paths, key=lambda p: -file_score.get(p, 0))
        if ranked:
            chosen.append(ranked[0])
    chosen.sort(key=lambda p: -file_score.get(p, 0))

    parts: list[str] = []
    used = 0
    for i, path in enumerate(chosen):
        content = project_files.get(path, "")
        if not content:
            continue
        header = f"\n### {path}\n```\n"
        footer = "\n```\n"
        block = header + content + footer
        if used + len(block) <= budget_chars:
            parts.append(block)
            used += len(block)
            continue
        remaining = budget_chars - used - len(header) - len(footer) - 60
        if remaining > 800:
            parts.append(header + content[:remaining] + "\n...（已截断）" + footer)
            used = budget_chars
        unshown = len(chosen) - i - (1 if remaining > 800 else 0)
        if unshown > 0:
            parts.append(f"\n（其余 {unshown} 个文件因预算限制未展示）\n")
        break

    return "".join(parts) or "（无关键文件源码）"


def _fmt_module_skeletons(
    modules: list[dict],
    ast_model: ProjectAST,
    top_n: int = 6,
) -> str:
    """每模块列出按调用度排序的核心符号——给 LLM 一份"哪里值得看"的索引。

    输出包含装饰器、参数、入度/出度、起始行号——LLM 据此引用具体符号，
    避免凭模块描述脑补不存在的设计（baseline 编"INIT/PAID/CLOSED 状态机"的根因）。
    """
    if not modules:
        return "（无模块）"

    in_deg: dict[str, int] = {}
    out_deg: dict[str, int] = {}
    for e in ast_model.edges:
        if e.callee_resolved:
            in_deg[e.callee_resolved] = in_deg.get(e.callee_resolved, 0) + 1
        out_deg[e.caller] = out_deg.get(e.caller, 0) + 1

    sections: list[str] = []
    for idx, m in enumerate(modules):
        paths = set(m.get("paths") or [])
        symbols = [
            s for s in ast_model.definitions.values()
            if s.file in paths and s.kind != "class"
        ]
        # 入口符号优先；其次按 (in+out) 度数；再按 line_start 稳定排序
        symbols.sort(
            key=lambda s: (
                -1 if s.is_entry else 0,
                -(in_deg.get(s.qualified_name, 0) + out_deg.get(s.qualified_name, 0)),
                s.line_start,
            )
        )
        symbols = symbols[:top_n]
        if not symbols:
            continue

        lines = [f"#### `{module_id(idx)}` 核心符号"]
        for s in symbols:
            entry_mark = " [入口]" if s.is_entry else ""
            deco = f" {' '.join(s.decorators)}" if s.decorators else ""
            params = ", ".join(s.params) if s.params else ""
            ind = in_deg.get(s.qualified_name, 0)
            outd = out_deg.get(s.qualified_name, 0)
            lines.append(
                f"  - `{s.name}({params})`{deco}{entry_mark}  "
                f"[in:{ind} out:{outd}]  L{s.line_start}"
            )
        sections.append("\n".join(lines))

    if not sections:
        return "（AST 中无可索引的核心符号）"
    return "\n\n".join(sections)


def _count_commented_methods(content: str) -> int:
    """启发式计文件中被注释掉的方法/类数量（演进证据信号）。

    iter1 漏代根因：LLM 没意识到 createVoucherOrder 所在文件含 4 段历史实现的注释代码块，
    把 SimpleRedisLock 内部的 v1/v2 当成项目级演进。把这个信号显式喂给 step1 选符号时
    优先权倾斜——含 N 段注释方法的文件就是"演进的中心"。

    匹配 `/* ... */` 块且内容含 Java 类成员关键字的算一段。覆盖 TestProject 这类
    Java 项目，对 Python/Rust 注释风格不敏感（按需扩展）。
    """
    if not content or "/*" not in content:
        return 0
    count = 0
    keywords = ("private ", "public ", "protected ", "@Override", "@Transactional")
    i = 0
    n = len(content)
    while i < n - 1:
        if content[i:i + 2] == "/*":
            j = content.find("*/", i + 2)
            if j < 0:
                break
            block = content[i + 2:j]
            if any(kw in block for kw in keywords):
                count += 1
            i = j + 2
        else:
            i += 1
    return count


def _build_commented_count_index(
    project_files: dict[str, str],
    relevant_paths: set[str],
) -> dict[str, int]:
    """对 relevant_paths 范围内的文件预计算注释方法数。

    范围限定避免对项目所有文件扫描——只算后续会引用的模块文件。
    """
    out: dict[str, int] = {}
    for p in relevant_paths:
        content = project_files.get(p)
        if content:
            out[p] = _count_commented_methods(content)
    return out


def _fmt_symbol_catalog_for_recon(
    modules: list[dict],
    ast_model: ProjectAST,
    project_files: dict[str, str],
    top_n: int = 8,
) -> str:
    """Step1 侦察专用：紧凑的函数级符号目录，每行带 qualified_name。

    iter2 增强：每个符号末尾附加"该文件含 N 段历史注释代码"——给 step1 一个倾斜信号，
    优先选演进证据丰富的文件，避免再把 SimpleRedisLock 内部演进当成项目级演进。
    """
    if not modules:
        return "（无模块）"

    in_deg: dict[str, int] = {}
    out_deg: dict[str, int] = {}
    for e in ast_model.edges:
        if e.callee_resolved:
            in_deg[e.callee_resolved] = in_deg.get(e.callee_resolved, 0) + 1
        out_deg[e.caller] = out_deg.get(e.caller, 0) + 1

    relevant_paths: set[str] = set()
    for m in modules:
        relevant_paths.update(m.get("paths") or [])
    commented_idx = _build_commented_count_index(project_files, relevant_paths)

    sections: list[str] = []
    for idx, m in enumerate(modules):
        paths = set(m.get("paths") or [])
        symbols = [
            s for s in ast_model.definitions.values()
            if s.file in paths and s.kind != "class"
        ]
        symbols.sort(
            key=lambda s: (
                -1 if s.is_entry else 0,
                -(in_deg.get(s.qualified_name, 0) + out_deg.get(s.qualified_name, 0)),
                s.line_start,
            )
        )
        symbols = symbols[:top_n]
        if not symbols:
            continue

        lines = [f"### `{module_id(idx)}` {m.get('name', '')}"]
        for s in symbols:
            ind = in_deg.get(s.qualified_name, 0)
            outd = out_deg.get(s.qualified_name, 0)
            entry_mark = " [入口]" if s.is_entry else ""
            cmt = commented_idx.get(s.file, 0)
            cmt_mark = f" ⚑含{cmt}段历史注释代码" if cmt >= 2 else ""
            lines.append(
                f"  - `{s.qualified_name}` "
                f"[in:{ind} out:{outd}]{entry_mark} L{s.line_start}{cmt_mark}"
            )
        sections.append("\n".join(lines))

    if not sections:
        return "（AST 中无可索引的核心符号）"
    return "\n\n".join(sections)


def _fmt_relevant_symbols(
    selected: list[dict],
    ast_model: ProjectAST,
    project_files: dict[str, str],
) -> str:
    """把 step1 输出的 [{qualified_name, reason}] 渲染成 step2 的"清单"区。

    iter2 增强：标注"所在文件含 N 段历史注释代码"——让 step2 在写演进轨迹时知道
    哪些符号才有真正的代际证据。
    """
    if not selected:
        return "（侦察阶段未选出任何符号）"
    lines = []
    for item in selected:
        qn = item.get("qualified_name", "")
        reason = item.get("reason", "")
        s = ast_model.definitions.get(qn)
        if s is None:
            continue
        cmt = _count_commented_methods(project_files.get(s.file, ""))
        cmt_mark = f"，⚑含{cmt}段历史注释代码" if cmt >= 2 else ""
        loc = f"{s.file} L{s.line_start}-L{s.line_end}{cmt_mark}"
        lines.append(f"- `{qn}` ({loc}) — {reason}")
    return "\n".join(lines) or "（侦察阶段选出的符号在 AST 中均未找到）"


def _fmt_focused_symbols_source(
    selected: list[dict],
    ast_model: ProjectAST,
    project_files: dict[str, str],
    *,
    context_lines: int = 20,
    full_file_threshold: float = 0.5,
    budget_chars: int = 12000,
) -> str:
    """按 step1 选中符号抽对应源码片段。

    策略：
    - 按 file 分组，多符号同文件合并为一段连续片段（最早 line_start - N 到最晚 line_end + N）
    - 若片段范围 ≥ 50% 文件，直接给全文（保留注释代码块作为演进证据）
    - 文件按内部符号 (in+out) 度数总和降序排，预算用尽就截断

    与 chapter 的 `_fmt_key_files_source` 区别：那个按"模块的最 hot 文件"挑，
    可能漏掉专题相关但调用度低的方法；这里完全由 step1 的相关性主导。
    """
    by_file: dict[str, list[SymbolDef]] = {}
    for item in selected:
        qn = item.get("qualified_name", "")
        s = ast_model.definitions.get(qn)
        if s is None:
            logger.debug("Recon selected qualified_name not in AST: %r", qn)
            continue
        by_file.setdefault(s.file, []).append(s)

    if not by_file:
        return "（侦察阶段选出的符号在 AST 中均未找到）"

    in_deg: dict[str, int] = {}
    out_deg: dict[str, int] = {}
    for e in ast_model.edges:
        if e.callee_resolved:
            in_deg[e.callee_resolved] = in_deg.get(e.callee_resolved, 0) + 1
        out_deg[e.caller] = out_deg.get(e.caller, 0) + 1

    file_score: dict[str, int] = {}
    for f, syms in by_file.items():
        file_score[f] = sum(
            in_deg.get(s.qualified_name, 0) + out_deg.get(s.qualified_name, 0)
            for s in syms
        )

    files_ranked = sorted(by_file.keys(), key=lambda f: -file_score[f])

    parts: list[str] = []
    used = 0
    for i, f in enumerate(files_ranked):
        content = project_files.get(f, "")
        if not content:
            continue
        lines = content.splitlines()
        n = len(lines) or 1
        syms = sorted(by_file[f], key=lambda s: s.line_start)
        min_start = max(1, min(s.line_start for s in syms) - context_lines)
        max_end = min(n, max(s.line_end for s in syms) + context_lines)

        if (max_end - min_start + 1) >= n * full_file_threshold:
            snippet = content
            range_label = "全文"
        else:
            snippet = "\n".join(lines[min_start - 1: max_end])
            range_label = f"L{min_start}-L{max_end}"

        symbol_list = ", ".join(f"{s.name}(L{s.line_start})" for s in syms)
        header = (
            f"\n### {f} [{range_label}]\n"
            f"包含符号：{symbol_list}\n```\n"
        )
        footer = "\n```\n"
        block = header + snippet + footer

        if used + len(block) <= budget_chars:
            parts.append(block)
            used += len(block)
            continue

        remaining = budget_chars - used - len(header) - len(footer) - 60
        if remaining > 800:
            parts.append(header + snippet[:remaining] + "\n...（已截断）" + footer)
            used = budget_chars
        unshown = len(files_ranked) - i - (1 if remaining > 800 else 0)
        if unshown > 0:
            parts.append(f"\n（其余 {unshown} 个文件因预算限制未展示）\n")
        break

    return "".join(parts) or "（无可读源码）"


def _fmt_run_hints(doc_bundle: DocBundle) -> str:
    if not doc_bundle.run_hints:
        return "（未发现明显的运行线索）"
    parts = []
    for source, content in doc_bundle.run_hints.items():
        truncated = content if len(content) <= 800 else content[:800] + "\n...（已截断）"
        parts.append(f"### {source}\n```\n{truncated}\n```")
    return "\n\n".join(parts)
