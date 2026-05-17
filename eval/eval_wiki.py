"""Wiki 文档评测脚本。

指标：
1. 模块覆盖率 — wiki 中引用的模块 / DB 中全部模块
2. 实体准确率 — content_md 中的标识符是否存在于 symbols 表
3. 代码引用有效性 — code_refs 的 file+line_range 是否在实际文件中存在
4. 内部链接完整性 — outgoing_links 引用的 page_id 是否真实存在
5. 内容非空率 — 非 category 页面是否有有效内容

运行: python eval/eval_wiki.py --project <project_name>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.utils import (
    check_file_lines,
    extract_identifiers_strict,
    get_connection,
    load_module_paths,
    load_project_files,
    load_symbols,
    pct,
    report_path,
    write_report,
)


def load_wiki(project_name: str):
    conn = get_connection()
    try:
        doc_row = conn.execute(
            "SELECT index_json FROM wiki_documents WHERE project_name = ?",
            (project_name,),
        ).fetchone()
        if doc_row is None:
            print(f"[ERROR] No wiki found for project '{project_name}'", file=sys.stderr)
            sys.exit(1)

        pages = conn.execute(
            "SELECT page_id, type, title, content_md, metadata_json "
            "FROM wiki_pages WHERE project_name = ?",
            (project_name,),
        ).fetchall()
        return doc_row, pages
    finally:
        conn.close()


def eval_coverage(pages, module_paths: set[str]) -> dict:
    """Check how many modules are referenced in wiki pages."""
    referenced_modules = set()
    for page in pages:
        if page["type"] != "module":
            continue
        meta = json.loads(page["metadata_json"]) if page["metadata_json"] else {}
        module_info = meta.get("module_info")
        if module_info and module_info.get("files"):
            referenced_modules.update(module_info["files"])
    covered = referenced_modules & module_paths
    return {
        "total_modules": len(module_paths),
        "covered_modules": len(covered),
        "coverage_pct": pct(len(covered), len(module_paths)),
        "missing": sorted(module_paths - covered)[:20],
    }


def eval_entity_accuracy(pages, symbols: set[str]) -> dict:
    """Check if identifiers mentioned in content exist in the symbols table."""
    total_checked = 0
    total_found = 0
    page_details = []

    for page in pages:
        content = page["content_md"]
        if not content:
            continue
        identifiers = extract_identifiers_strict(content)
        if not identifiers:
            continue

        found = identifiers & symbols
        total_checked += len(identifiers)
        total_found += len(found)

        if len(identifiers) > 0:
            page_details.append({
                "page_id": page["page_id"],
                "identifiers_checked": len(identifiers),
                "identifiers_found": len(found),
                "accuracy_pct": pct(len(found), len(identifiers)),
            })

    return {
        "total_identifiers_checked": total_checked,
        "total_identifiers_found": total_found,
        "overall_accuracy_pct": pct(total_found, total_checked),
        "worst_pages": sorted(page_details, key=lambda x: x["accuracy_pct"])[:5],
    }


def eval_code_refs(pages, project_files: dict[str, str]) -> dict:
    """Validate code_refs point to real file locations."""
    total_refs = 0
    valid_refs = 0
    invalid = []

    for page in pages:
        meta = json.loads(page["metadata_json"]) if page["metadata_json"] else {}
        code_refs = meta.get("code_refs", {})
        for ref_id, ref in code_refs.items():
            total_refs += 1
            file_path = ref.get("file", "")
            start = ref.get("start_line", 0)
            end = ref.get("end_line", 0)
            if check_file_lines(project_files, file_path, start, end):
                valid_refs += 1
            else:
                invalid.append({
                    "page_id": page["page_id"],
                    "ref_id": ref_id,
                    "file": file_path,
                    "lines": f"{start}-{end}",
                })

    return {
        "total_refs": total_refs,
        "valid_refs": valid_refs,
        "validity_pct": pct(valid_refs, total_refs),
        "invalid_samples": invalid[:10],
    }


def eval_internal_links(pages) -> dict:
    """Check that outgoing_links reference existing page_ids."""
    all_page_ids = {page["page_id"] for page in pages}
    broken = []
    total_links = 0

    for page in pages:
        meta = json.loads(page["metadata_json"]) if page["metadata_json"] else {}
        links = meta.get("outgoing_links", [])
        for link in links:
            total_links += 1
            if link not in all_page_ids:
                broken.append({"from_page": page["page_id"], "target": link})

    return {
        "total_links": total_links,
        "broken_links": len(broken),
        "broken_details": broken[:10],
    }


def eval_content_completeness(pages) -> dict:
    """Check non-category pages have meaningful content."""
    non_cat_pages = [p for p in pages if p["type"] != "category"]
    non_empty = [p for p in non_cat_pages if p["content_md"] and len(p["content_md"]) > 100]
    empty_pages = [
        {"page_id": p["page_id"], "type": p["type"], "length": len(p["content_md"] or "")}
        for p in non_cat_pages
        if not p["content_md"] or len(p["content_md"]) <= 100
    ]

    return {
        "total_content_pages": len(non_cat_pages),
        "non_empty_pages": len(non_empty),
        "completeness_pct": pct(len(non_empty), len(non_cat_pages)),
        "empty_or_short": empty_pages[:10],
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate wiki document quality")
    parser.add_argument("--project", required=True, help="Project name")
    args = parser.parse_args()

    project_name = args.project
    doc_row, pages = load_wiki(project_name)

    symbols = load_symbols(project_name)
    module_paths = load_module_paths(project_name)
    project_files = load_project_files(project_name)

    results = {
        "project": project_name,
        "total_pages": len(pages),
        "coverage": eval_coverage(pages, module_paths),
        "entity_accuracy": eval_entity_accuracy(pages, symbols),
        "code_refs": eval_code_refs(pages, project_files),
        "internal_links": eval_internal_links(pages),
        "content_completeness": eval_content_completeness(pages),
    }

    out = report_path("wiki", project_name)
    write_report(results, out)

    print(
        f"Wiki eval: coverage={results['coverage']['coverage_pct']}%, "
        f"entity_accuracy={results['entity_accuracy']['overall_accuracy_pct']}%, "
        f"code_refs_valid={results['code_refs']['validity_pct']}%, "
        f"broken_links={results['internal_links']['broken_links']}, "
        f"content_complete={results['content_completeness']['completeness_pct']}%"
    )


if __name__ == "__main__":
    main()
