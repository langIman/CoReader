"""QA 问答评测脚本。

指标：
1. 关键词命中率 — 回答中是否包含 expected_keywords
2. 代码引用准确率 — 返回的 code_refs 文件是否匹配预期
3. 完成率 — stop_reason == "completed" 的比例
4. 工具使用合理性 — deep 模式下是否使用了工具

运行: python eval/eval_qa.py --project <project_name> [--base-url http://localhost:8000]
需要后端运行中，会实际调用 /api/qa/ask 接口。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.utils import pct, report_path, write_report

GOLDEN_PATH = Path(__file__).parent / "qa_golden.json"


def load_golden() -> list[dict]:
    if not GOLDEN_PATH.exists():
        print(f"[ERROR] Golden set not found: {GOLDEN_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        return json.load(f)


def ask_question(base_url: str, project_name: str, question: str, mode: str) -> dict:
    """Call the QA SSE endpoint and collect the full response."""
    url = f"{base_url}/api/qa/ask"
    payload = json.dumps({
        "project_name": project_name,
        "question": question,
        "mode": mode,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )

    content_parts = []
    code_refs = {}
    tool_events = []
    stop_reason = None

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            buffer = ""
            for chunk in iter(lambda: resp.read(1024), b""):
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    event_type = None
                    data = None
                    for line in frame.split("\n"):
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data = line[5:].strip()

                    if not data:
                        continue
                    try:
                        parsed = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    if event_type == "token":
                        content_parts.append(parsed.get("delta", ""))
                    elif event_type == "code_refs":
                        code_refs = parsed.get("refs", {})
                    elif event_type == "tool_result":
                        tool_events.append(parsed)
                    elif event_type == "done":
                        if parsed.get("content"):
                            content_parts = [parsed["content"]]
                        stop_reason = parsed.get("stop_reason", "completed")
    except (urllib.error.URLError, TimeoutError) as e:
        return {"error": str(e)}

    content = "".join(content_parts)
    return {
        "content": content,
        "code_refs": code_refs,
        "tool_events": tool_events,
        "stop_reason": stop_reason or "completed",
    }


def eval_keyword_hit(golden: list[dict], responses: list[dict]) -> dict:
    """Check if expected keywords appear in responses."""
    total_keywords = 0
    hit_keywords = 0
    per_question = []

    for g, r in zip(golden, responses):
        if "error" in r:
            continue
        expected = g.get("expected_keywords", [])
        content_lower = r["content"].lower()
        hits = [kw for kw in expected if kw.lower() in content_lower]
        total_keywords += len(expected)
        hit_keywords += len(hits)
        per_question.append({
            "question": g["question"][:60],
            "expected": expected,
            "hit": hits,
            "hit_pct": pct(len(hits), len(expected)),
        })

    return {
        "total_keywords": total_keywords,
        "hit_keywords": hit_keywords,
        "overall_hit_pct": pct(hit_keywords, total_keywords),
        "per_question": per_question,
    }


def eval_code_ref_accuracy(golden: list[dict], responses: list[dict]) -> dict:
    """Check if returned code_refs match expected files."""
    total = 0
    precision_sum = 0
    recall_sum = 0

    for g, r in zip(golden, responses):
        if "error" in r:
            continue
        expected_files = set(g.get("expected_code_refs", []))
        if not expected_files:
            continue
        actual_files = {ref.get("file", "") for ref in r["code_refs"].values()} if r["code_refs"] else set()
        total += 1
        if actual_files:
            precision = len(actual_files & expected_files) / len(actual_files)
            precision_sum += precision
        recall = len(actual_files & expected_files) / len(expected_files)
        recall_sum += recall

    return {
        "questions_with_expected_refs": total,
        "avg_precision_pct": round(precision_sum / max(total, 1) * 100, 1),
        "avg_recall_pct": round(recall_sum / max(total, 1) * 100, 1),
    }


def eval_completion_rate(responses: list[dict]) -> dict:
    """Check stop_reason distribution."""
    valid = [r for r in responses if "error" not in r]
    completed = sum(1 for r in valid if r["stop_reason"] == "completed")
    return {
        "total": len(valid),
        "completed": completed,
        "completion_pct": pct(completed, len(valid)),
        "reasons": sorted({r["stop_reason"] for r in valid}),
    }


def eval_tool_usage(golden: list[dict], responses: list[dict]) -> dict:
    """Check that deep mode responses actually used tools."""
    deep_count = 0
    used_tools = 0

    for g, r in zip(golden, responses):
        if "error" in r:
            continue
        if g.get("mode") == "deep":
            deep_count += 1
            if r["tool_events"]:
                used_tools += 1

    return {
        "deep_questions": deep_count,
        "used_tools": used_tools,
        "tool_usage_pct": pct(used_tools, deep_count),
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate QA system quality")
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Backend URL")
    args = parser.parse_args()

    golden = load_golden()
    print(f"Loaded {len(golden)} golden questions, sending to {args.base_url}...")

    responses = []
    for i, g in enumerate(golden):
        print(f"  [{i+1}/{len(golden)}] {g['question'][:50]}...", end=" ", flush=True)
        start = time.time()
        resp = ask_question(args.base_url, args.project, g["question"], g.get("mode", "deep"))
        elapsed = time.time() - start
        if "error" in resp:
            print(f"ERROR: {resp['error']}")
        else:
            print(f"OK ({elapsed:.1f}s, {len(resp['content'])} chars)")
        responses.append(resp)

    results = {
        "project": args.project,
        "questions_total": len(golden),
        "questions_errored": sum(1 for r in responses if "error" in r),
        "keyword_hit": eval_keyword_hit(golden, responses),
        "code_ref_accuracy": eval_code_ref_accuracy(golden, responses),
        "completion_rate": eval_completion_rate(responses),
        "tool_usage": eval_tool_usage(golden, responses),
    }

    out = report_path("qa", args.project)
    write_report(results, out)

    print(
        f"\nQA eval: keyword_hit={results['keyword_hit']['overall_hit_pct']}%, "
        f"code_ref_recall={results['code_ref_accuracy']['avg_recall_pct']}%, "
        f"completion={results['completion_rate']['completion_pct']}%, "
        f"tool_usage={results['tool_usage']['tool_usage_pct']}%"
    )


if __name__ == "__main__":
    main()
