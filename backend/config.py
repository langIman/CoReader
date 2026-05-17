import os


def _parse_optional_bool(name: str) -> bool | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3.6-plus")
# 摘要类"短而快"的任务用 fast 模型；空则回落到 QWEN_MODEL（即与默认一致，等价禁用分层）
QWEN_FAST_MODEL = os.getenv("QWEN_FAST_MODEL", "MiniMax-M2.7") or QWEN_MODEL
QWEN_ENABLE_THINKING = _parse_optional_bool("QWEN_ENABLE_THINKING")

MAX_FILE_SIZE = 1024 * 1024  # 1MB
ALLOWED_EXTENSIONS = {
    # 代码文件
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
    ".c", ".cpp", ".h", ".rb", ".php", ".swift", ".kt",
    # 配置文件
    ".json", ".yaml", ".yml", ".toml", ".xml",
    # 文档 / 其他
    ".md", ".txt", ".html", ".css", ".sql", ".sh", ".dockerfile",
}
# AST 静态分析支持的文件类型
AST_EXTENSIONS = {".py", ".rs", ".java"}

# 扩展名 → 语言标识映射
_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".rs": "rust",
    ".java": "java",
}


def is_ast_file(path: str) -> bool:
    """Check if a file path has an AST-analyzable extension."""
    return any(path.endswith(ext) for ext in AST_EXTENSIONS)


def get_file_language(path: str) -> str | None:
    """根据扩展名返回语言标识。"""
    for ext, lang in _LANG_MAP.items():
        if path.endswith(ext):
            return lang
    return None
MAX_PROJECT_SIZE = 10 * 1024 * 1024  # 10MB total
MAX_PROJECT_FILES = 200

# 摘要生成配置
SUMMARY_FUNC_LINES = 7          # 每个函数/类提取的行数
SUMMARY_TRUNCATION_PERCENT = 0.3  # 文件截断上限（30%）

# Wiki 模块页源码预算（字符数）；中英混合代码 ≈ 3 字符/token，90000 ≈ 30k tokens
MODULE_CODE_BUDGET_CHARS = 90000

# Agent 系统配置（从 services/agents/config.py 迁移）
MAX_WORKER_CONCURRENCY = 5           # Worker 最大并发数（摘要/wiki 页生成）。
# 注：sub2api 网关账号池容量有限，10 并发会触发 429 "All available accounts exhausted"。
# 5 是实测下安全值，超过需先验证当前套餐池容量。
