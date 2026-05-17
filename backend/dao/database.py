import sqlite3
import os
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "summaries.db")

# Wiki schema 版本。每次结构不兼容变更时 bump 一下，启动时会一次性清空旧 Wiki。
WIKI_SCHEMA_VERSION = 2


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS summaries (
            path TEXT NOT NULL,
            type TEXT NOT NULL,
            summary TEXT NOT NULL,
            project_name TEXT NOT NULL,
            PRIMARY KEY (path, project_name)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS symbols (
            qualified_name TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            file TEXT NOT NULL,
            line_start INTEGER NOT NULL,
            line_end INTEGER NOT NULL,
            decorators TEXT DEFAULT '[]',
            docstring TEXT,
            params TEXT DEFAULT '[]',
            is_entry INTEGER DEFAULT 0,
            project_name TEXT NOT NULL,
            PRIMARY KEY (qualified_name, project_name)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS call_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller TEXT NOT NULL,
            callee_name TEXT NOT NULL,
            callee_resolved TEXT,
            file TEXT DEFAULT '',
            line INTEGER DEFAULT 0,
            call_type TEXT DEFAULT 'direct',
            resolution_method TEXT,
            project_name TEXT NOT NULL
        )
    """)
    # 兼容老库：resolution_method 列后加，PRAGMA 检测后 ALTER TABLE 添加
    cols = {row[1] for row in conn.execute("PRAGMA table_info(call_edges)")}
    if "resolution_method" not in cols:
        conn.execute("ALTER TABLE call_edges ADD COLUMN resolution_method TEXT")
        logger.info("call_edges schema migrated: +resolution_method")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS modules (
            path TEXT NOT NULL,
            line_count INTEGER DEFAULT 0,
            symbol_count INTEGER DEFAULT 0,
            imports TEXT DEFAULT '[]',
            project_name TEXT NOT NULL,
            PRIMARY KEY (path, project_name)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wiki_documents (
            project_name TEXT PRIMARY KEY,
            project_hash TEXT,
            generated_at TEXT,
            index_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wiki_pages (
            page_id TEXT NOT NULL,
            project_name TEXT NOT NULL,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            path TEXT,
            status TEXT NOT NULL,
            content_md TEXT,
            metadata_json TEXT,
            PRIMARY KEY (project_name, page_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS project_files (
            project_name TEXT NOT NULL,
            path TEXT NOT NULL,
            content TEXT NOT NULL,
            PRIMARY KEY (project_name, path)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS qa_conversations (
            id           TEXT PRIMARY KEY,
            project_name TEXT NOT NULL,
            title        TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_qa_conv_project
            ON qa_conversations(project_name, updated_at DESC)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS qa_messages (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id  TEXT NOT NULL,
            role             TEXT NOT NULL,
            content          TEXT NOT NULL,
            mode             TEXT,
            tool_events_json TEXT,
            code_refs_json   TEXT,
            stop_reason      TEXT,
            created_at       TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES qa_conversations(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_qa_msg_conv
            ON qa_messages(conversation_id, id)
    """)
    # 兼容老库：stop_reason 列后加（阶段 5）
    qa_msg_cols = {row[1] for row in conn.execute("PRAGMA table_info(qa_messages)")}
    if "stop_reason" not in qa_msg_cols:
        conn.execute("ALTER TABLE qa_messages ADD COLUMN stop_reason TEXT")
        logger.info("qa_messages schema migrated: +stop_reason")
    # 兼容老库：tool_chain_json 列后加（上下文管理对齐 CC）
    if "tool_chain_json" not in qa_msg_cols:
        conn.execute("ALTER TABLE qa_messages ADD COLUMN tool_chain_json TEXT")
        logger.info("qa_messages schema migrated: +tool_chain_json")

    # ----------------- Quiz 自测功能（QUIZ_FEATURE_PLAN.md §2.2）-----------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quiz_sessions (
            id             TEXT PRIMARY KEY,
            project_name   TEXT NOT NULL,
            mode           TEXT NOT NULL,
            source_id      TEXT,
            title          TEXT NOT NULL,
            status         TEXT NOT NULL DEFAULT 'generating',
            score          INTEGER NOT NULL DEFAULT 0,
            answered_count INTEGER NOT NULL DEFAULT 0,
            created_at     TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_quiz_sessions_project
            ON quiz_sessions(project_name, created_at DESC)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quiz_questions (
            id            TEXT PRIMARY KEY,
            session_id    TEXT NOT NULL,
            idx           INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            options_json  TEXT NOT NULL,
            correct_key   TEXT NOT NULL,
            code_ref_json TEXT,
            FOREIGN KEY (session_id) REFERENCES quiz_sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_quiz_questions_session
            ON quiz_questions(session_id, idx)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quiz_answers (
            session_id     TEXT NOT NULL,
            question_index INTEGER NOT NULL,
            chosen_key     TEXT NOT NULL,
            is_correct     INTEGER NOT NULL,
            answered_at    TEXT NOT NULL,
            PRIMARY KEY (session_id, question_index),
            FOREIGN KEY (session_id) REFERENCES quiz_sessions(id) ON DELETE CASCADE
        )
    """)

    # Wiki 结构变更的一次性清理：user_version 落后就清空 wiki_* 表
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current < WIKI_SCHEMA_VERSION:
        conn.execute("DELETE FROM wiki_documents")
        conn.execute("DELETE FROM wiki_pages")
        conn.execute(f"PRAGMA user_version = {WIKI_SCHEMA_VERSION}")
        logger.info(
            "Wiki schema bumped %d -> %d, cleared wiki_documents/wiki_pages",
            current, WIKI_SCHEMA_VERSION,
        )

    conn.commit()
    conn.close()
    logger.info("SQLite database initialized at %s", DB_PATH)
