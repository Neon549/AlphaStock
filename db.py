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

# 本地 pgvector Docker 开发库使用独立覆盖文件，避免改写包含线上/API 密钥的 .env。
local_pgvector_env_path = Path(__file__).resolve().parent / ".env.pgvector"
if local_pgvector_env_path.exists():
    load_dotenv(dotenv_path=local_pgvector_env_path, override=True)

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
                    connect_timeout=10,
                )
                print("PostgreSQL connection pool initialised")
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

_DDL_CORE = """
-- ── 用户认证 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL DEFAULT '',
    salt          TEXT NOT NULL DEFAULT '',
    email         TEXT,
    google_id     TEXT,
    token         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_users_username  ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email     ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id);
CREATE INDEX IF NOT EXISTS idx_users_token     ON users(token);

-- ── 登录 token ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tokens (
    token      TEXT PRIMARY KEY,
    username   TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_tokens_username ON tokens(username);
CREATE INDEX IF NOT EXISTS idx_tokens_expires ON tokens(expires_at);

-- ── 密码重置 token ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token      TEXT PRIMARY KEY,
    username   TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    used       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_prt_username ON password_reset_tokens(username);

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

-- Temporary upload sessions are tenant-owned.  The vector table is keyed by
-- session_id, so this small ownership table is the authorization boundary.
CREATE TABLE IF NOT EXISTS upload_sessions (
    session_id TEXT PRIMARY KEY,
    actor_id   TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours')
);
CREATE INDEX IF NOT EXISTS idx_upload_sessions_actor ON upload_sessions(actor_id, created_at DESC);

-- ── Control plane audit trail ───────────────────────────────────────
-- Event is the idempotency boundary.  Runs and steps are intentionally
-- separate from the investment memory tables: they describe execution, not
-- facts that should be injected into an LLM prompt.
CREATE TABLE IF NOT EXISTS agent_events (
    event_id    TEXT PRIMARY KEY,
    trigger     TEXT NOT NULL,
    channel     TEXT NOT NULL,
    session_id  TEXT,
    actor_id    TEXT,
    content_len INTEGER NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_events_session_received
    ON agent_events (session_id, received_at DESC);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id       TEXT PRIMARY KEY,
    event_id     TEXT NOT NULL REFERENCES agent_events(event_id),
    route        TEXT NOT NULL,
    status       TEXT NOT NULL,
    session_id   TEXT,
    result_meta  JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_event ON agent_runs (event_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_session_completed
    ON agent_runs (session_id, completed_at DESC);

CREATE TABLE IF NOT EXISTS agent_steps (
    id         BIGSERIAL PRIMARY KEY,
    run_id     TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    detail     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_steps_run ON agent_steps (run_id, step_index);

-- ── Agent memory (separate from raw chat logs and evidence/RAG) ──────
CREATE TABLE IF NOT EXISTS agent_session_memory (
    session_id TEXT PRIMARY KEY,
    actor_id   TEXT,
    memory     JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_session_memory_actor_updated
    ON agent_session_memory (actor_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS user_preferences (
    actor_id   TEXT PRIMARY KEY,
    preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_session_transcript (
    id         BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    actor_id   TEXT,
    run_id     TEXT,
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_transcript_session_created
    ON agent_session_transcript (session_id, created_at DESC);

-- Durable background work. Jobs contain only transcript ID ranges and metadata,
-- never a copied prompt/transcript, so a worker can be restarted safely.
CREATE TABLE IF NOT EXISTS agent_memory_maintenance_jobs (
    job_id       TEXT PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('extract', 'sleep_consolidate')),
    status       TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed', 'skipped')),
    session_id   TEXT,
    actor_id     TEXT,
    source_from_id BIGINT,
    source_to_id BIGINT,
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at   TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_memory_maintenance_jobs_status_created
    ON agent_memory_maintenance_jobs (status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_maintenance_active_extract
    ON agent_memory_maintenance_jobs (kind, session_id)
    WHERE status IN ('queued', 'running') AND kind = 'extract';

-- Candidate experience is deliberately separate from approved Markdown.
-- A candidate never reaches the retrieval index until a human approves it.
CREATE TABLE IF NOT EXISTS agent_memory_candidates (
    candidate_id TEXT PRIMARY KEY,
    status       TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    title        TEXT NOT NULL,
    category     TEXT NOT NULL,
    content      TEXT NOT NULL,
    source_run_id TEXT,
    requested_by TEXT,
    reviewer     TEXT,
    review_note  TEXT,
    approved_path TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_memory_candidates_status_created
    ON agent_memory_candidates (status, created_at DESC);

"""

_DDL_VECTOR = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS news_vectors (
    id          TEXT PRIMARY KEY,
    stock_code  TEXT NOT NULL,
    stock_name  TEXT NOT NULL,
    title       TEXT NOT NULL,
    full_text   TEXT NOT NULL,
    pub_time    TEXT,
    date        DATE,
    embedding   vector(768),
    indexed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_nv_stock ON news_vectors(stock_code);
CREATE INDEX IF NOT EXISTS idx_nv_date  ON news_vectors(date);

CREATE INDEX IF NOT EXISTS idx_nv_embedding
    ON news_vectors
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

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

-- ── 会话临时文档 RAG（上传财报/公告/研报）────────────────────────
CREATE TABLE IF NOT EXISTS uploaded_document_chunks (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    filename         TEXT NOT NULL,
    document_id      TEXT NOT NULL,
    document_version TEXT NOT NULL,
    chunk_index      INTEGER NOT NULL,
    page             INTEGER NOT NULL DEFAULT 0,
    parent_path      TEXT NOT NULL DEFAULT '正文',
    previous_id      TEXT,
    next_id          TEXT,
    content          TEXT NOT NULL,
    embedding        vector(768) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_udc_session_created
    ON uploaded_document_chunks (session_id, created_at);
-- 临时文档按 session 过滤后规模很小，当前刻意不建 HNSW；精确检索更简单。

CREATE TABLE IF NOT EXISTS agent_memory_chunks (
    id           TEXT PRIMARY KEY,
    source_path  TEXT NOT NULL,
    source_hash  TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    content      TEXT NOT NULL,
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding    vector(768) NOT NULL,
    indexed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_path, source_hash, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_amc_source ON agent_memory_chunks (source_path, indexed_at DESC);
-- Long-term operating knowledge is expected to grow to thousands of chunks;
-- use ANN here, while session-scoped uploads remain exact-search by design.
CREATE INDEX IF NOT EXISTS idx_amc_embedding
    ON agent_memory_chunks
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
            cur.execute(_DDL_CORE)
        conn.commit()
    print("Core database tables initialised")

    # pgvector 扩展可选，本地没装也能正常跑
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_DDL_VECTOR)
            conn.commit()
        print("pgvector tables initialised")
    except Exception as e:
        print(f"pgvector unavailable; vector tables skipped: {e}")


# ── 便捷查询函数 ──────────────────────────────────────────────────────


def execute(sql: str, params=None, fetch: str = None):
    """
    单次执行封装，适合简单的增删改查。

    fetch:
        None    → 不返回结果（INSERT/UPDATE/DELETE）
        "one"   → fetchone()
        "all"   → fetchall()
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
