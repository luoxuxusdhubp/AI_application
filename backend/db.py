import sqlite3
from contextlib import closing
import logging
import os


# 数据库文件路径（使用既有 wechat_friends.db，保持兼容前数据）
DB_PATH = os.path.join(os.path.dirname(__file__), 'wechat_friends.db')


def _exec(conn: sqlite3.Connection, sql: str, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur


def ensure_friends_table(conn: sqlite3.Connection):
    # 好友表：存基础资料与分组关联
    _exec(conn, """
    CREATE TABLE IF NOT EXISTS friends (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        uid TEXT NOT NULL,
        msg TEXT,
        group_id INTEGER,
        created_at DATETIME DEFAULT (DATETIME('now','localtime')),
        updated_at DATETIME DEFAULT (DATETIME('now','localtime'))
    )
    """)


def ensure_groups_table(conn: sqlite3.Connection):
    # 分组表：唯一名称约束
    _exec(conn, """
    CREATE TABLE IF NOT EXISTS groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        created_at DATETIME DEFAULT (DATETIME('now','localtime')),
        updated_at DATETIME DEFAULT (DATETIME('now','localtime'))
    )
    """)


def ensure_send_history_table(conn: sqlite3.Connection):
    # 发送历史：记录内容与统计
    _exec(conn, """
    CREATE TABLE IF NOT EXISTS send_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        groups TEXT,
        friend_ids TEXT,
        total INTEGER,
        success_count INTEGER,
        created_at DATETIME DEFAULT (DATETIME('now','localtime'))
    )
    """)


def ensure_scheduled_jobs_table(conn: sqlite3.Connection):
    # 定时任务：run_at 使用统一字符串格式
    _exec(conn, """
    CREATE TABLE IF NOT EXISTS scheduled_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        groups TEXT,
        group_ids TEXT,
        run_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        total INTEGER,
        success_count INTEGER,
        error TEXT,
        created_at DATETIME DEFAULT (DATETIME('now','localtime')),
        updated_at DATETIME DEFAULT (DATETIME('now','localtime'))
    )
    """)

def ensure_qa_kb_table(conn: sqlite3.Connection):
    # 问答知识库表：记录问答条目
    _exec(conn, """
    CREATE TABLE IF NOT EXISTS qa_kb (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        created_at DATETIME DEFAULT (DATETIME('now','localtime')),
        updated_at DATETIME DEFAULT (DATETIME('now','localtime'))
    )
    """)


def ensure_ai_settings_table(conn: sqlite3.Connection):
    # 系统提示词设置表：仅需一条记录，保存当前 system 提示词
    _exec(conn, """
    CREATE TABLE IF NOT EXISTS ai_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        system_prompt TEXT NOT NULL,
        created_at DATETIME DEFAULT (DATETIME('now','localtime')),
        updated_at DATETIME DEFAULT (DATETIME('now','localtime'))
    )
    """)
    # 目标联系人用于限制自动回复范围。旧数据库没有这些列时做无损迁移。
    cur = _exec(conn, "PRAGMA table_info(ai_settings)")
    columns = {row[1] for row in cur.fetchall()}
    if "target_contact" not in columns:
        _exec(conn, "ALTER TABLE ai_settings ADD COLUMN target_contact TEXT NOT NULL DEFAULT ''")
    if "auto_reply_mode" not in columns:
        _exec(conn, "ALTER TABLE ai_settings ADD COLUMN auto_reply_mode TEXT NOT NULL DEFAULT 'draft'")
    # 聊天记录风格分析、表情包发送两个开关（旧库无损迁移）
    if "persona_enabled" not in columns:
        _exec(conn, "ALTER TABLE ai_settings ADD COLUMN persona_enabled INTEGER NOT NULL DEFAULT 1")
    if "sticker_enabled" not in columns:
        _exec(conn, "ALTER TABLE ai_settings ADD COLUMN sticker_enabled INTEGER NOT NULL DEFAULT 0")
    # 确保至少有一条默认记录
    cur = _exec(conn, "SELECT COUNT(*) AS c FROM ai_settings")
    cnt = cur.fetchone()[0]
    if not cnt:
        default_prompt = (
            "你是知识库问答助手。请严格依据知识库提供的答案作答，"
            "回答简洁、礼貌、可执行；如知识库不足以覆盖问题，说明需要更多信息，"
            "并建议补充知识库。优先使用中文。"
        )
        _exec(
            conn,
            "INSERT INTO ai_settings(system_prompt, target_contact, auto_reply_mode) VALUES (?, '', 'draft')",
            (default_prompt,),
        )


def ensure_chat_history_table(conn: sqlite3.Connection):
    # 聊天记录表：存储好友聊天历史
    _exec(conn, """
    CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        friend_id INTEGER NOT NULL,
        friend_name TEXT NOT NULL,
        sender TEXT NOT NULL,
        content TEXT NOT NULL,
        msg_type TEXT,
        msg_time TEXT,
        created_at DATETIME DEFAULT (DATETIME('now','localtime')),
        FOREIGN KEY (friend_id) REFERENCES friends(id)
    )
    """)


def ensure_reply_drafts_table(conn: sqlite3.Connection):
    # 自动回复候选草稿：必须人工批准后才会发送
    _exec(conn, """
    CREATE TABLE IF NOT EXISTS reply_drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_name TEXT NOT NULL,
        incoming_message TEXT NOT NULL,
        options_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        selected_index INTEGER,
        created_at DATETIME DEFAULT (DATETIME('now','localtime')),
        updated_at DATETIME DEFAULT (DATETIME('now','localtime')),
        sent_at DATETIME
    )
    """)
    _exec(
        conn,
        "CREATE INDEX IF NOT EXISTS idx_reply_drafts_status_created "
        "ON reply_drafts(status, created_at DESC)",
    )
    # 每个候选回复可附带一个表情包（与 options_json 等长的 JSON 数组）
    cur = _exec(conn, "PRAGMA table_info(reply_drafts)")
    columns = {row[1] for row in cur.fetchall()}
    if "stickers_json" not in columns:
        _exec(conn, "ALTER TABLE reply_drafts ADD COLUMN stickers_json TEXT")


def ensure_persona_tables(conn: sqlite3.Connection):
    # 聊天语料：从微信读取的历史消息，用于风格分析与相似情境检索
    _exec(conn, """
    CREATE TABLE IF NOT EXISTS chat_corpus (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_name TEXT NOT NULL,
        is_self INTEGER NOT NULL,
        content TEXT NOT NULL,
        msg_time TEXT NOT NULL DEFAULT '',
        seq INTEGER NOT NULL DEFAULT 0,
        created_at DATETIME DEFAULT (DATETIME('now','localtime'))
    )
    """)
    _exec(
        conn,
        "CREATE INDEX IF NOT EXISTS idx_chat_corpus_chat ON chat_corpus(chat_name, seq)",
    )
    # 风格画像：每个聊天对象一条，由语料统计得到
    _exec(conn, """
    CREATE TABLE IF NOT EXISTS persona_profiles (
        chat_name TEXT PRIMARY KEY,
        profile_json TEXT NOT NULL,
        updated_at DATETIME DEFAULT (DATETIME('now','localtime'))
    )
    """)


def ensure_all_tables():
    """
    初始化所有必要的数据表
    """
    try:
        with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as conn:
            ensure_friends_table(conn)
            ensure_groups_table(conn)
            ensure_send_history_table(conn)
            ensure_scheduled_jobs_table(conn)
            ensure_qa_kb_table(conn)
            ensure_ai_settings_table(conn)
            ensure_chat_history_table(conn)
            ensure_reply_drafts_table(conn)
            ensure_persona_tables(conn)
            conn.commit()
    except Exception as e:
        logging.exception("初始化数据库失败")
        raise
