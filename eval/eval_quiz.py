"""Quiz 出题评测脚本。

指标：
1. 选项格式合规率 — 4 选项非空、correct_key 合法、explanation 非空
2. 答案实体可验证率 — 正确选项中的标识符是否存在于 symbols 表
3. 题目多样性 — code_ref 覆盖的不同文件数
4. 自洽性 — 给 LLM 题目+源码，验证能否选出正确答案

运行: python eval/eval_quiz.py --project <project_name> [--session <session_id>] [--skip-llm]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from eval.utils import (
    extract_identifiers,
    get_connection,
    load_project_files,
    load_symbols,
    pct,
    report_path,
    write_report,
)


def load_quiz_sessions(project_name: str, session_id: str | None) -> list[dict]:
    conn = get_connection()
    try:
        if session_id:
            rows = conn.execute(
                "SELECT id, title, status FROM quiz_sessions WHERE id = ?",
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, status FROM quiz_sessions "
                "WHERE project_name = ? AND status = 'ready' ORDER BY created_at DESC",
                (project_name,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def load_questions(session_id: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT idx, question_text, options_json, correct_key, code_ref_json "
            "FROM quiz_questions WHERE session_id = ? ORDER BY idx ASC",
            (session_id,),
        ).fetchall()
        results = []
        for r in rows:
            options = json.loads(r["options_json"])
            code_ref = json.loads(r["code_ref_json"]) if r["code_ref_json"] else None
            results.append({
                "index": r["idx"],
                "question_text": r["question_text"],
                "options": options,
                "correct_key": r["correct_key"],
                "code_ref": code_ref,
            })
        return results
    finally:
        conn.close()


def eval_format_compliance(questions: list[dict]) -> dict:
    """Check that each question has valid structure."""
    total = len(questions)
    valid = 0
    issues = []

    for q in questions:
        ok = True
        if len(q["options"]) != 4:
            issues.append({"index": q["index"], "issue": f"has {len(q['options'])} options"})
            ok = False
        if q["correct_key"] not in ("A", "B", "C", "D"):
            issues.append({"index": q["index"], "issue": f"invalid correct_key: {q['correct_key']}"})
            ok = False
        for opt in q["options"]:
            if not opt.get("text", "").strip():
                issues.append({"index": q["index"], "issue": f"option {opt.get('key')} has empty text"})
                ok = False
            if not opt.get("explanation", "").strip():
                issues.append({"index": q["index"], "issue": f"option {opt.get('key')} has empty explanation"})
                ok = False
        if ok:
            valid += 1

    return {
        "total": total,
        "valid": valid,
        "compliance_pct": pct(valid, total),
        "issues": issues[:15],
    }


def eval_entity_verifiability(questions: list[dict], symbols: set[str]) -> dict:
    """Check if the correct answer mentions real code entities."""
    total = 0
    verifiable = 0

    for q in questions:
        correct_opt = next((o for o in q["options"] if o["key"] == q["correct_key"]), None)
        if not correct_opt:
            continue
        total += 1
        identifiers = extract_identifiers(correct_opt["text"])
        if identifiers & symbols:
            verifiable += 1

    return {
        "total_checked": total,
        "verifiable": verifiable,
        "verifiability_pct": pct(verifiable, total),
    }


def eval_diversity(questions: list[dict]) -> dict:
    """Check code_ref coverage across different files."""
    files_seen = set()
    questions_with_ref = 0

    for q in questions:
        if q["code_ref"] and q["code_ref"].get("file"):
            questions_with_ref += 1
            files_seen.add(q["code_ref"]["file"])

    return {
        "total_questions": len(questions),
        "questions_with_code_ref": questions_with_ref,
        "unique_files_referenced": len(files_seen),
        "diversity_ratio": round(len(files_seen) / max(len(questions), 1), 2),
        "files": sorted(files_seen),
    }


async def eval_self_consistency(
    questions: list[dict],
    project_files: dict[str, str],
) -> dict:
    """Give LLM each question + relevant source code, check if it picks the correct answer."""
    from backend.services.llm.llm_service import call_qwen

    total = 0
    correct = 0
    details = []

    for q in questions:
        code_context = ""
        if q["code_ref"] and q["code_ref"].get("file"):
            file_path = q["code_ref"]["file"]
            content = project_files.get(file_path, "")
            if content:
                start = q["code_ref"].get("line_start", 1)
                end = q["code_ref"].get("line_end", len(content.split("\n")))
                lines = content.split("\n")[max(0, start - 1):end]
                code_context = f"\n\n相关源码 ({file_path}, 行 {start}-{end}):\n```\n" + "\n".join(lines) + "\n```"

        options_text = "\n".join(
            f"{o['key']}. {o['text']}" for o in q["options"]
        )
        prompt = (
            f"请回答以下选择题，只输出选项字母（A/B/C/D），不要解释。\n\n"
            f"题目：{q['question_text']}\n\n"
            f"选项：\n{options_text}"
            f"{code_context}"
        )

        try:
            response = await call_qwen(
                system_prompt="你是一个代码理解专家，正在做代码相关的选择题。只回答选项字母。",
                user_prompt=prompt,
                enable_thinking=False,
            )
            answer = response.strip().upper()
            # Extract just the letter if model says more
            for letter in ("A", "B", "C", "D"):
                if letter in answer:
                    answer = letter
                    break

            total += 1
            is_correct = answer == q["correct_key"]
            if is_correct:
                correct += 1
            else:
                details.append({
                    "index": q["index"],
                    "expected": q["correct_key"],
                    "got": answer,
                    "question": q["question_text"][:80],
                })
        except Exception as e:
            details.append({"index": q["index"], "error": str(e)})

    return {
        "total_tested": total,
        "correct": correct,
        "accuracy_pct": pct(correct, total),
        "wrong_answers": details[:10],
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate quiz generation quality")
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument("--session", default=None, help="Specific session ID (default: all ready sessions)")
    parser.add_argument("--skip-llm", action="store_true", help="Skip self-consistency test (no LLM calls)")
    args = parser.parse_args()

    sessions = load_quiz_sessions(args.project, args.session)
    if not sessions:
        print(f"[ERROR] No quiz sessions found for project '{args.project}'", file=sys.stderr)
        sys.exit(1)

    symbols = load_symbols(args.project)
    project_files = load_project_files(args.project)

    all_results = []
    for session in sessions:
        questions = load_questions(session["id"])
        if not questions:
            continue

        result = {
            "session_id": session["id"],
            "title": session["title"],
            "question_count": len(questions),
            "format_compliance": eval_format_compliance(questions),
            "entity_verifiability": eval_entity_verifiability(questions, symbols),
            "diversity": eval_diversity(questions),
        }

        if not args.skip_llm:
            consistency = asyncio.run(eval_self_consistency(questions, project_files))
            result["self_consistency"] = consistency

        all_results.append(result)

    report = {
        "project": args.project,
        "sessions_evaluated": len(all_results),
        "results": all_results,
    }

    out = report_path("quiz", args.project)
    write_report(report, out)

    for r in all_results:
        summary = (
            f"Quiz [{r['title'][:30]}]: "
            f"format={r['format_compliance']['compliance_pct']}%, "
            f"entity_verify={r['entity_verifiability']['verifiability_pct']}%, "
            f"diversity={r['diversity']['diversity_ratio']}"
        )
        if "self_consistency" in r:
            summary += f", self_consistency={r['self_consistency']['accuracy_pct']}%"
        print(summary)


if __name__ == "__main__":
    main()
