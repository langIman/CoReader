# Eval — CoReader 自动评测框架

对三个核心功能（Wiki 文档生成、QA 问答、Quiz 出题）进行可量化的质量评估。

## 运行方式

```bash
# 从项目根目录运行
# Wiki 评测（纯本地，读 DB）
python eval/eval_wiki.py --project <project_name>

# Quiz 评测（默认含 LLM 自洽性测试）
python eval/eval_quiz.py --project <project_name>
python eval/eval_quiz.py --project <project_name> --session <session_id>
python eval/eval_quiz.py --project <project_name> --skip-llm  # 跳过 LLM 调用

# QA 评测（需要后端运行中）
PYTHONPATH=. uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
python eval/eval_qa.py --project <project_name>
python eval/eval_qa.py --project <project_name> --base-url http://localhost:8000
```

## 前置条件

- 已上传并分析过项目（DB 中有 symbols/modules/summaries 数据）
- Wiki 评测：需先生成过 wiki
- Quiz 评测：需有 status="ready" 的 quiz session
- QA 评测：需后端运行中

## 指标体系

### Wiki 文档

| 指标 | 含义 |
|------|------|
| coverage | wiki 中引用的模块数 / 项目全部模块数 |
| entity_accuracy | markdown 中提到的标识符在 symbols 表中存在的比例 |
| code_refs_valid | code_refs 指向的文件+行范围确实存在 |
| broken_links | 内部链接 (#wiki:page_id) 指向不存在的页面数 |
| content_complete | 非 category 页面有有效内容（>100 字符）的比例 |

### QA 问答

| 指标 | 含义 |
|------|------|
| keyword_hit | 回答中包含预期关键词的比例 |
| code_ref_recall | 返回的代码引用覆盖了预期文件的比例 |
| completion | stop_reason 为 completed 的比例 |
| tool_usage | deep 模式下实际使用了工具的比例 |

### Quiz 出题

| 指标 | 含义 |
|------|------|
| format_compliance | 选项结构完整（4 选项、非空、有解释）的比例 |
| entity_verifiability | 正确答案中的标识符能在 symbols 表验证的比例 |
| diversity | code_ref 覆盖的不同文件数 / 题目总数 |
| self_consistency | LLM 给源码后能做对题目的比例（>95% 正常，<80% 说明题目有问题） |

## 输出

每次运行产出一个 JSON report 到 `eval/report/` 目录，命名格式：
```
{wiki|qa|quiz}_{project_name}_{timestamp}.json
```

终端同时打印 one-liner summary。

## 扩展 QA Golden Set

编辑 `eval/qa_golden.json`，每条格式：
```json
{
  "question": "问题文本",
  "expected_keywords": ["关键词1", "关键词2"],
  "expected_code_refs": ["expected/file/path.py"],
  "mode": "deep"
}
```

`mode` 可选 `"fast"` 或 `"deep"`。
