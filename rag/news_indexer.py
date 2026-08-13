#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rag/news_indexer.py  ──  新闻向量库（pgvector 版）

替代 ChromaDB，使用 PostgreSQL + pgvector。
表 news_vectors 由 db.init_db() 创建。

三大功能不变：
  1. bulk_index()        批量入库
  2. start_stream()      流式更新（后台线程）
  3. delete_expired()    过期删除
"""

import hashlib
import re
import threading
import time
from typing import Any
from datetime import datetime, timedelta

import schedule
import jieba
from psycopg2.extras import execute_values

from db import get_conn

# ── 配置 ──────────────────────────────────────────────────────────────

NEWS_EXPIRE_DAYS = 30
STREAM_INTERVAL_MINUTES = 5
EMBED_MODEL = "shibing624/text2vec-base-chinese"  # 768 维，与 DDL 一致
NEWS_CONTENT_MAX_CHARS = 1200

_BASE_WATCH_LIST = [
    ("000001", "平安银行"),
    ("600036", "招商银行"),
    ("601166", "兴业银行"),
    ("600000", "浦发银行"),
    ("600519", "贵州茅台"),
    ("000858", "五粮液"),
    ("000651", "格力电器"),
    ("000333", "美的集团"),
    ("000725", "京东方A"),
    ("002475", "立讯精密"),
    ("603501", "韦尔股份"),
    ("300750", "宁德时代"),
    ("002594", "比亚迪"),
    ("601012", "隆基绿能"),
    ("000002", "万科A"),
    ("001979", "招商蛇口"),
    ("600276", "恒瑞医药"),
    ("000538", "云南白药"),
]

# Keep the public evaluation queries in the same refresh path as production
# watch stocks.  Otherwise an evaluation can look at a stale slice of the
# remote corpus even when the live application is being refreshed.
EVAL_STOCKS = [
    ("600519", "贵州茅台"),
    ("300750", "宁德时代"),
    ("000858", "五粮液"),
    ("600036", "招商银行"),
    ("002415", "海康威视"),
    ("601138", "工业富联"),
    ("300124", "汇川技术"),
    ("600487", "亨通光电"),
    ("002475", "立讯精密"),
    ("603501", "韦尔股份"),
]

WATCH_LIST = list(dict.fromkeys(_BASE_WATCH_LIST + EVAL_STOCKS))

# ── Embedding 模型单例 ────────────────────────────────────────────────

_embed_model: Any | None = None
_embed_lock = threading.Lock()


def _get_embed_model() -> Any:
    """Load the embedding dependency only when an embedding is actually needed.

    Importing this module is part of document metadata tests and API startup.  The
    model package is a runtime capability, not a requirement for simply importing
    the RAG interfaces.
    """
    global _embed_model
    if _embed_model is None:
        with _embed_lock:
            if _embed_model is None:
                from sentence_transformers import SentenceTransformer

                print("[NewsIndexer] 加载 Embedding 模型...")
                _embed_model = SentenceTransformer(EMBED_MODEL)
                print("[NewsIndexer] Embedding 模型加载完成")
    return _embed_model


def _embed(texts: list[str]) -> list[list[float]]:
    model = _get_embed_model()
    return model.encode(texts, normalize_embeddings=True).tolist()


# ── 工具函数 ──────────────────────────────────────────────────────────


def _news_id(stock_code: str, title: str) -> str:
    return hashlib.md5(f"{stock_code}_{title}".encode("utf-8")).hexdigest()


def _parse_news(raw_news: str, stock_code: str, stock_name: str) -> list[dict]:
    items = []
    for line in raw_news.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("【"):
            continue
        try:
            end = line.index("】")
            pub_time = line[1:end].strip()
            title = line[end + 1 :].strip()
            if not title:
                continue
            items.append(
                {
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "pub_time": pub_time,
                    "title": title,
                    "full_text": f"{stock_name}（{stock_code}）{pub_time} {title}",
                    "date": (
                        pub_time[:10]
                        if len(pub_time) >= 10
                        else datetime.now().strftime("%Y-%m-%d")
                    ),
                }
            )
        except (ValueError, IndexError):
            continue
    return items


def _clean_news_field(value: Any) -> str:
    if value is None or value != value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _build_news_item(row: Any, stock_code: str, stock_name: str) -> dict | None:
    pub_time = _clean_news_field(row.get("发布时间", ""))
    title = _clean_news_field(row.get("新闻标题", ""))
    if not title:
        return None
    content = _clean_news_field(row.get("新闻内容", ""))[:NEWS_CONTENT_MAX_CHARS]
    source = _clean_news_field(row.get("文章来源", ""))
    source_url = _clean_news_field(row.get("新闻链接", ""))
    fields = [f"{stock_name}（{stock_code}）", pub_time, f"标题：{title}"]
    if content and content != title:
        fields.append(f"内容：{content}")
    if source:
        fields.append(f"来源：{source}")
    if source_url:
        fields.append(f"链接：{source_url}")
    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "pub_time": pub_time,
        "title": title,
        "full_text": " ".join(field for field in fields if field),
        "date": pub_time[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", pub_time) else datetime.now().strftime("%Y-%m-%d"),
    }


def _fetch_news_items(stock_code: str, stock_name: str, limit: int = 50) -> list[dict]:
    try:
        import akshare as ak

        time.sleep(0.5)
        df = ak.stock_news_em(symbol=stock_code)
        if df.empty:
            return []
        return [
            item
            for _, row in df.head(limit).iterrows()
            if (item := _build_news_item(row, stock_code, stock_name)) is not None
        ]
    except Exception as e:
        print(f"[NewsIndexer] 拉取 {stock_code} 新闻失败: {e}")
        return []


def _fetch_news(stock_code: str) -> str:
    """Backward-compatible title-only representation for legacy callers."""

    return "\n".join(
        f"【{item['pub_time']}】{item['title']}"
        for item in _fetch_news_items(stock_code, stock_code)
    )


def news_evidence_snippet(title: str, full_text: str, query: str, max_chars: int = 360) -> str:
    """Keep full text for ranking but return only query-relevant evidence."""

    text = _clean_news_field(full_text)
    if not text:
        return title
    body_match = re.search(r"内容：(.*?)(?=\s来源：|\s链接：|$)", text)
    body = body_match.group(1).strip() if body_match else ""
    source_match = re.search(r"来源：(.*?)(?=\s链接：|$)", text)
    link_match = re.search(r"链接：(\S+)", text)
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?；;])\s*", body) if part.strip()]
    if not sentences and body:
        sentences = [body]

    query_terms = {
        token.lower()
        for token in jieba.lcut(query or "")
        if token.strip() and (len(token.strip()) > 1 or token.strip().isdigit())
    }
    query_numbers = set(re.findall(r"\d+(?:\.\d+)?", query or ""))

    def relevance(sentence: str) -> tuple[float, int]:
        sentence_terms = {token.lower() for token in jieba.lcut(sentence) if token.strip()}
        overlap = sum(len(term) for term in query_terms & sentence_terms)
        number_overlap = len(query_numbers & set(re.findall(r"\d+(?:\.\d+)?", sentence)))
        return overlap + number_overlap * 4.0, -sentences.index(sentence)

    ranked = sorted(sentences, key=relevance, reverse=True)
    chosen: list[str] = []
    used = len(title) + 8
    for sentence in ranked:
        if chosen and relevance(sentence)[0] <= 0:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        chosen.append(sentence[:remaining])
        used += len(chosen[-1])
        if len(chosen) >= 2:
            break
    if not chosen and sentences:
        chosen = [sentences[0][: max(0, max_chars - used)]]

    output = [f"标题：{title}"]
    if chosen:
        output.append(f"证据：{' '.join(chosen)}")
    if source_match:
        output.append(f"来源：{source_match.group(1).strip()}")
    if link_match:
        output.append(f"链接：{link_match.group(1)}")
    return " ".join(output)


# ── 核心写入 ──────────────────────────────────────────────────────────


def _insert_news_batch(items: list[dict], *, return_stats: bool = False) -> int | dict[str, int]:
    """
    批量写入 news_vectors，并仅用信息量更大的文本富化已有 ID。
    默认返回实际新增条数；刷新任务可请求完整统计。
    """
    if not items:
        empty = {"added": 0, "updated": 0, "embedded": 0}
        return empty if return_stats else 0

    identified_by_id: dict[str, dict] = {}
    for item in items:
        doc_id = _news_id(item["stock_code"], item["title"])
        current = identified_by_id.get(doc_id)
        if current is None or len(item["full_text"]) > len(current["full_text"]):
            identified_by_id[doc_id] = item
    identified = list(identified_by_id.items())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, LENGTH(COALESCE(full_text, '')) FROM news_vectors WHERE id = ANY(%s)",
                ([doc_id for doc_id, _ in identified],),
            )
            existing_lengths = {row[0]: int(row[1] or 0) for row in cur.fetchall()}

    pending = [
        (doc_id, item)
        for doc_id, item in identified
        if doc_id not in existing_lengths
        or len(item["full_text"]) > existing_lengths[doc_id]
    ]
    if not pending:
        empty = {"added": 0, "updated": 0, "embedded": 0}
        return empty if return_stats else 0

    texts = [item["full_text"] for _, item in pending]
    embeddings = _embed(texts)

    with get_conn() as conn:
        with conn.cursor() as cur:
            inserted = execute_values(
                cur,
                """
                INSERT INTO news_vectors
                    (id, stock_code, stock_name, title, full_text,
                     pub_time, date, embedding)
                VALUES %s
                ON CONFLICT (id) DO UPDATE SET
                    stock_name = EXCLUDED.stock_name,
                    full_text = EXCLUDED.full_text,
                    pub_time = EXCLUDED.pub_time,
                    date = EXCLUDED.date,
                    embedding = EXCLUDED.embedding,
                    indexed_at = NOW()
                WHERE LENGTH(EXCLUDED.full_text) > LENGTH(COALESCE(news_vectors.full_text, ''))
                RETURNING id
                """,
                [
                    (
                        doc_id,
                        item["stock_code"],
                        item["stock_name"],
                        item["title"],
                        item["full_text"],
                        item["pub_time"],
                        item["date"] or None,
                        str(emb),  # pgvector 接受 '[0.1, 0.2, ...]' 格式
                    )
                    for (doc_id, item), emb in zip(pending, embeddings)
                ],
                template="(%s, %s, %s, %s, %s, %s, %s, %s::vector)",
                page_size=100,
                fetch=True,
            )
        conn.commit()
    changed_ids = {row[0] for row in inserted}
    added = sum(doc_id not in existing_lengths for doc_id in changed_ids)
    stats = {
        "added": added,
        "updated": len(changed_ids) - added,
        "embedded": len(pending),
    }
    return stats if return_stats else added


# ── 功能一：批量入库 ──────────────────────────────────────────────────


def bulk_index(
    stock_list: list[tuple] = None,
    limit_per_stock: int = 50,
    *,
    return_stats: bool = False,
):
    if stock_list is None:
        stock_list = WATCH_LIST

    total_added = 0
    total_updated = 0
    total_embedded = 0
    print(f"[NewsIndexer] 开始批量入库，目标：{len(stock_list)} 只股票")

    for stock_code, stock_name in stock_list:
        try:
            items = _fetch_news_items(stock_code, stock_name, limit_per_stock)
            if not items:
                print(f"   ⚠️ {stock_name} 无新闻，跳过")
                continue
            stats = _insert_news_batch(items, return_stats=True)
            total_added += stats["added"]
            total_updated += stats["updated"]
            total_embedded += stats["embedded"]
            print(f"   ✅ {stock_name}：新增 {stats['added']} 条，正文富化 {stats['updated']} 条")
        except Exception as e:
            print(f"   ❌ {stock_name} 入库失败: {e}")
        time.sleep(0.5)

    result = {"added": total_added, "updated": total_updated, "embedded": total_embedded}
    print(f"[NewsIndexer] 批量入库完成，新增 {total_added} 条，正文富化 {total_updated} 条")
    return result if return_stats else total_added


# ── 功能二：流式更新 ──────────────────────────────────────────────────

_stream_running = False
_stream_thread: threading.Thread | None = None


def stream_update_once(stock_list: list[tuple] = None) -> int:
    if stock_list is None:
        stock_list = WATCH_LIST

    added_total = 0
    updated_total = 0
    for stock_code, stock_name in stock_list:
        try:
            items = _fetch_news_items(stock_code, stock_name)
            if not items:
                continue
            stats = _insert_news_batch(items, return_stats=True)
            added_total += stats["added"]
            updated_total += stats["updated"]
        except Exception as e:
            print(f"[Stream] {stock_name} 更新失败: {e}")

    if added_total or updated_total:
        print(f"[Stream] {datetime.now().strftime('%H:%M:%S')} 新增 {added_total} 条，正文富化 {updated_total} 条")
    return added_total


def start_stream(interval_minutes: int = STREAM_INTERVAL_MINUTES):
    global _stream_thread, _stream_running
    if _stream_running:
        print("[Stream] 流式更新已在运行")
        return

    _stream_running = True

    def _run():
        print(f"[Stream] 启动流式更新，间隔 {interval_minutes} 分钟")
        stream_update_once()
        schedule.every(interval_minutes).minutes.do(stream_update_once)
        while _stream_running:
            schedule.run_pending()
            time.sleep(30)
        print("[Stream] 流式更新已停止")

    _stream_thread = threading.Thread(target=_run, daemon=True)
    _stream_thread.start()


def stop_stream():
    global _stream_running
    _stream_running = False


# ── 功能三：过期删除 ──────────────────────────────────────────────────


def delete_expired(expire_days: int = NEWS_EXPIRE_DAYS):
    cutoff = (datetime.now() - timedelta(days=expire_days)).strftime("%Y-%m-%d")
    print(f"[Cleanup] 删除 {cutoff} 之前的新闻...")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM news_vectors WHERE date < %s", (cutoff,))
            deleted = cur.rowcount
        conn.commit()
    print(f"[Cleanup] 删除 {deleted} 条过期新闻")


def schedule_daily_cleanup(hour: int = 2):
    schedule.every().day.at(f"{hour:02d}:00").do(delete_expired)
    print(f"[Cleanup] 已设置每天 {hour:02d}:00 自动清理")


# ── 检索接口 ──────────────────────────────────────────────────────────


def retrieve_news(
    query: str,
    stock_code: str = None,
    k: int = 10,
    days: int = 7,
) -> str:
    """
    pgvector 语义检索新闻。
    余弦相似度，HNSW 索引，返回 Top-K。
    """
    query_emb = _embed([query])[0]
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    if stock_code:
        sql = """
            SELECT title, stock_name, pub_time, full_text
            FROM news_vectors
            WHERE stock_code = %s AND date >= %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        params = (stock_code, cutoff, str(query_emb), k)
    else:
        sql = """
            SELECT title, stock_name, pub_time, full_text
            FROM news_vectors
            WHERE date >= %s AND stock_code ~ '^[0-9]{6}$'
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        params = (cutoff, str(query_emb), k)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    if not rows:
        return f"最近 {days} 天内未找到相关新闻"

    return "\n".join(
        news_evidence_snippet(r[0], r[3], query)
        if r[3]
        else f"【{r[1]} | {r[2]}】{r[0]}"
        for r in rows
    )


def retrieve_news_corpus(
    stock_code: str,
    days: int = 30,
    limit: int = 500,
) -> list[str]:
    """Return a bounded stock-scoped title corpus without embedding inference."""

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT title, stock_name, pub_time
                FROM news_vectors
                WHERE stock_code = %s AND date >= %s
                ORDER BY date DESC, pub_time DESC
                LIMIT %s
                """,
                (stock_code, cutoff, limit),
            )
            rows = cur.fetchall()
    return [f"【{name} | {pub_time}】{title}" for title, name, pub_time in rows]


def _table_exists(table: str) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
                    (table,),
                )
                return cur.fetchone() is not None
    except Exception:
        return False


def get_stats() -> dict:
    if not _table_exists("news_vectors"):
        return {
            "total_news": 0,
            "expire_days": NEWS_EXPIRE_DAYS,
            "watch_stocks": len(WATCH_LIST),
            "stream_running": _stream_running,
            "pgvector": False,
        }
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (WHERE stock_code ~ '^[0-9]{6}$'),
                    COUNT(DISTINCT stock_code) FILTER (WHERE stock_code ~ '^[0-9]{6}$')
                FROM news_vectors
                """
            )
            total, valid_total, indexed_stocks = cur.fetchone()
    return {
        "total_news": total,
        "valid_news": valid_total,
        "invalid_stock_code_news": total - valid_total,
        "indexed_stocks": indexed_stocks,
        "expire_days": NEWS_EXPIRE_DAYS,
        "watch_stocks": len(WATCH_LIST),
        "stream_running": _stream_running,
        "pgvector": True,
    }


# ── 一键启动 ──────────────────────────────────────────────────────────


def start_news_system(
    bulk_first: bool = True,
    stream_interval: int = STREAM_INTERVAL_MINUTES,
    cleanup_hour: int = 2,
):
    stats = get_stats()
    if not stats.get("pgvector", True):
        print("[NewsSystem] pgvector 未安装，新闻 RAG 功能不可用，跳过启动")
        return

    if bulk_first and stats["total_news"] == 0:
        print("[NewsSystem] 新闻库为空，开始批量初始化...")
        bulk_index()
    else:
        print(f"[NewsSystem] 新闻库已有 {stats['total_news']} 条，跳过批量入库")

    start_stream(interval_minutes=stream_interval)
    schedule_daily_cleanup(hour=cleanup_hour)
    print(f"[NewsSystem] 启动完成：{get_stats()}")
