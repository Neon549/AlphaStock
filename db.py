<<<<<<< Updated upstream
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db.py  ──  PostgreSQL 统一连接层
替代项目中所有散落的 sqlite3.connect() 调用

依赖：
    pip install psycopg2-binary pgvector

.env 新增：
    POSTGRES_DSN=postgresql://user:password@localhost:5432/alphastock
"""

import os
import threading
from contextlib import contextmanager
from pathlib import Path

import psycopg2
import psycopg2.pool
from dotenv import load_dotenv

# ── 加载环境变量 ──────────────────────────────────────────────────────
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

POSTGRES_DSN = os.getenv(
    "POSTGRES_DSN",
    "",
)

# ── 连接池（线程安全，min=2 max=10）──────────────────────────────────
_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=10,
                    dsn=POSTGRES_DSN,
                )
                print("✅ PostgreSQL 连接池已初始化")
    return _pool


@contextmanager
def get_conn():
    """
    上下文管理器，自动归还连接到池。

    用法：
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(...)
            conn.commit()
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ── 建表 DDL ─────────────────────────────────────────────────────────

_DDL = """
-- 启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- ── 用户认证 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL DEFAULT '',
    salt          TEXT NOT NULL DEFAULT '',
    google_id     TEXT,
    token         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_users_username  ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id);
CREATE INDEX IF NOT EXISTS idx_users_token     ON users(token);

-- ── 登录 token ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tokens (
    token      TEXT PRIMARY KEY,
    username   TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 对话历史 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations (
    id         SERIAL PRIMARY KEY,
    username   TEXT NOT NULL,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conv_username   ON conversations(username);
CREATE INDEX IF NOT EXISTS idx_conv_session    ON conversations(session_id);

-- ── Agent 长期记忆 ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trading_decisions (
    id                  SERIAL PRIMARY KEY,
    stock_code          TEXT NOT NULL,
    analysis_date       DATE NOT NULL,
    decision            TEXT NOT NULL,
    fundamental_summary TEXT,
    technical_summary   TEXT,
    sentiment_summary   TEXT,
    target_price        REAL,
    stop_loss           REAL,
    actual_result       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_td_stock ON trading_decisions(stock_code);

CREATE TABLE IF NOT EXISTS analysis_reflections (
    id         SERIAL PRIMARY KEY,
    stock_code TEXT NOT NULL,
    reflection TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ar_stock ON analysis_reflections(stock_code);

CREATE TABLE IF NOT EXISTS backtest_results (
    id             SERIAL PRIMARY KEY,
    stock_code     TEXT NOT NULL,
    strategy       TEXT NOT NULL,
    result_summary TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_br_stock ON backtest_results(stock_code);

-- ── LangGraph checkpoint（替代 checkpoints.db）────────────────────
-- 注：使用官方 langgraph-checkpoint-postgres 时此表由库自动创建
-- 这里预留，手动初始化时用
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id    TEXT NOT NULL,
    checkpoint   JSONB NOT NULL,
    metadata     JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (thread_id)
);

-- ── 对话记录持久化 ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations_store (
    id         TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    messages   TEXT NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cs_username ON conversations_store(username);
CREATE INDEX IF NOT EXISTS idx_cs_updated  ON conversations_store(updated_at);

-- ── pgvector：新闻向量表 ──────────────────────────────────────────
-- embedding 维度 768（text2vec-base-chinese 输出维度）
CREATE TABLE IF NOT EXISTS news_vectors (
    id          TEXT PRIMARY KEY,               -- MD5(stock_code + title)
    stock_code  TEXT NOT NULL,
    stock_name  TEXT NOT NULL,
    title       TEXT NOT NULL,
    full_text   TEXT NOT NULL,
    pub_time    TEXT,
    date        DATE,
    embedding   vector(768),                    -- pgvector 字段
    indexed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_nv_stock ON news_vectors(stock_code);
CREATE INDEX IF NOT EXISTS idx_nv_date  ON news_vectors(date);

-- HNSW 向量索引（余弦相似度，比 IVFFlat 更适合实时插入）
CREATE INDEX IF NOT EXISTS idx_nv_embedding
    ON news_vectors
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ── pgvector：策略向量表 ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS strategy_vectors (
    id         TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    metadata   JSONB,
    embedding  vector(768),
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sv_embedding
    ON strategy_vectors
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
"""


def init_db():
    """
    初始化所有表（幂等，可重复执行）
    在 main.py 启动时调用一次即可。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
    print("✅ 数据库表初始化完成（PostgreSQL + pgvector）")


# ── 便捷查询函数 ──────────────────────────────────────────────────────


def execute(sql: str, params=None, fetch: str = None):
    """
    单次执行封装，适合简单的增删改查。

    fetch:
        None    → 不返回结果（INSERT/UPDATE/DELETE）
        "one"   → fetchone()
        "all"   → fetchall()

    用法：
        execute("INSERT INTO users ...", (username, hash, salt))
        row = execute("SELECT * FROM users WHERE username=%s", (u,), fetch="one")
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if fetch == "one":
                result = cur.fetchone()
            elif fetch == "all":
                result = cur.fetchall()
            else:
                result = None
        conn.commit()
    return result


if __name__ == "__main__":
    init_db()
=======
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db.py  ──  PostgreSQL 统一连接层
替代项目中所有散落的 sqlite3.connect() 调用

依赖：
    pip install psycopg2-binary pgvector

.env 新增：
    POSTGRES_DSN=postgresql://user:password@localhost:5432/alphastock
"""

import os
import threading
from contextlib import contextmanager
from pathlib import Path

import psycopg2
import psycopg2.pool
from dotenv import load_dotenv

# ── 加载环境变量 ──────────────────────────────────────────────────────
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "")

# ── 连接池（线程安全，min=2 max=10）──────────────────────────────────
_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=10,
                    dsn=POSTGRES_DSN,
                )
                print("✅ PostgreSQL 连接池已初始化")
    return _pool


@contextmanager
def get_conn():
    """
    上下文管理器，自动归还连接到池。

    用法：
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(...)
            conn.commit()
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ── 建表 DDL ─────────────────────────────────────────────────────────

_DDL = """
-- 启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- ── 用户认证 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL DEFAULT '',
    salt          TEXT NOT NULL DEFAULT '',
    google_id     TEXT,
    token         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_users_username  ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id);
CREATE INDEX IF NOT EXISTS idx_users_token     ON users(token);

-- ── 登录 token ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tokens (
    token      TEXT PRIMARY KEY,
    username   TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 对话历史 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations (
    id         SERIAL PRIMARY KEY,
    username   TEXT NOT NULL,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conv_username   ON conversations(username);
CREATE INDEX IF NOT EXISTS idx_conv_session    ON conversations(session_id);

-- ── Agent 长期记忆 ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trading_decisions (
    id                  SERIAL PRIMARY KEY,
    stock_code          TEXT NOT NULL,
    analysis_date       DATE NOT NULL,
    decision            TEXT NOT NULL,
    fundamental_summary TEXT,
    technical_summary   TEXT,
    sentiment_summary   TEXT,
    target_price        REAL,
    stop_loss           REAL,
    actual_result       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_td_stock ON trading_decisions(stock_code);

CREATE TABLE IF NOT EXISTS analysis_reflections (
    id         SERIAL PRIMARY KEY,
    stock_code TEXT NOT NULL,
    reflection TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ar_stock ON analysis_reflections(stock_code);

CREATE TABLE IF NOT EXISTS backtest_results (
    id             SERIAL PRIMARY KEY,
    stock_code     TEXT NOT NULL,
    strategy       TEXT NOT NULL,
    result_summary TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_br_stock ON backtest_results(stock_code);

-- ── LangGraph checkpoint（替代 checkpoints.db）────────────────────
-- 注：使用官方 langgraph-checkpoint-postgres 时此表由库自动创建
-- 这里预留，手动初始化时用
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id    TEXT NOT NULL,
    checkpoint   JSONB NOT NULL,
    metadata     JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (thread_id)
);

-- ── 对话记录持久化 ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations_store (
    id         TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    messages   TEXT NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cs_username ON conversations_store(username);
CREATE INDEX IF NOT EXISTS idx_cs_updated  ON conversations_store(updated_at);

-- ── pgvector：新闻向量表 ──────────────────────────────────────────
-- embedding 维度 768（text2vec-base-chinese 输出维度）
CREATE TABLE IF NOT EXISTS news_vectors (
    id          TEXT PRIMARY KEY,               -- MD5(stock_code + title)
    stock_code  TEXT NOT NULL,
    stock_name  TEXT NOT NULL,
    title       TEXT NOT NULL,
    full_text   TEXT NOT NULL,
    pub_time    TEXT,
    date        DATE,
    embedding   vector(768),                    -- pgvector 字段
    indexed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_nv_stock ON news_vectors(stock_code);
CREATE INDEX IF NOT EXISTS idx_nv_date  ON news_vectors(date);

-- HNSW 向量索引（余弦相似度，比 IVFFlat 更适合实时插入）
CREATE INDEX IF NOT EXISTS idx_nv_embedding
    ON news_vectors
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ── pgvector：策略向量表 ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS strategy_vectors (
    id         TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    metadata   JSONB,
    embedding  vector(768),
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sv_embedding
    ON strategy_vectors
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
"""


def init_db():
    """
    初始化所有表（幂等，可重复执行）
    在 main.py 启动时调用一次即可。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
    print("✅ 数据库表初始化完成（PostgreSQL + pgvector）")


# ── 便捷查询函数 ──────────────────────────────────────────────────────


def execute(sql: str, params=None, fetch: str = None):
    """
    单次执行封装，适合简单的增删改查。

    fetch:
        None    → 不返回结果（INSERT/UPDATE/DELETE）
        "one"   → fetchone()
        "all"   → fetchall()

    用法：
        execute("INSERT INTO users ...", (username, hash, salt))
        row = execute("SELECT * FROM users WHERE username=%s", (u,), fetch="one")
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if fetch == "one":
                result = cur.fetchone()
            elif fetch == "all":
                result = cur.fetchall()
            else:
                result = None
        conn.commit()
    return result


if __name__ == "__main__":
    init_db()
>>>>>>> Stashed changes
