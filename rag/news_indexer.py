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
import threading
import time
from datetime import datetime, timedelta

import schedule
from sentence_transformers import SentenceTransformer

from db import get_conn

# ── 配置 ──────────────────────────────────────────────────────────────

NEWS_EXPIRE_DAYS = 30
STREAM_INTERVAL_MINUTES = 5
EMBED_MODEL = "shibing624/text2vec-base-chinese"  # 768 维，与 DDL 一致

WATCH_LIST = [
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

# ── Embedding 模型单例 ────────────────────────────────────────────────

_embed_model: SentenceTransformer | None = None
_embed_lock = threading.Lock()


def _get_embed_model() -> SentenceTransformer:
    global _embed_model
    if _embed_model is None:
        with _embed_lock:
            if _embed_model is None:
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


def _fetch_news(stock_code: str) -> str:
    try:
        import akshare as ak

        time.sleep(0.5)
        df = ak.stock_news_em(symbol=stock_code)
        if df.empty:
            return ""
        news_list = []
        for _, row in df.head(50).iterrows():
            pub_time = row.get("发布时间", "")
            title = row.get("新闻标题", "")
            if title:
                news_list.append(f"【{pub_time}】{title}")
        return "\n".join(news_list)
    except Exception as e:
        print(f"[NewsIndexer] 拉取 {stock_code} 新闻失败: {e}")
        return ""


# ── 核心写入 ──────────────────────────────────────────────────────────


def _insert_news_batch(items: list[dict]) -> int:
    """
    批量写入 news_vectors，跳过已存在的 ID。
    返回实际新增条数。
    """
    if not items:
        return 0

    texts = [it["full_text"] for it in items]
    embeddings = _embed(texts)

    added = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for item, emb in zip(items, embeddings):
                doc_id = _news_id(item["stock_code"], item["title"])
                cur.execute(
                    """
                    INSERT INTO news_vectors
                        (id, stock_code, stock_name, title, full_text,
                         pub_time, date, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        doc_id,
                        item["stock_code"],
                        item["stock_name"],
                        item["title"],
                        item["full_text"],
                        item["pub_time"],
                        item["date"] or None,
                        str(emb),  # pgvector 接受 '[0.1, 0.2, ...]' 格式
                    ),
                )
                if cur.rowcount:
                    added += 1
        conn.commit()
    return added


# ── 功能一：批量入库 ──────────────────────────────────────────────────


def bulk_index(stock_list: list[tuple] = None, limit_per_stock: int = 50):
    if stock_list is None:
        stock_list = WATCH_LIST

    total_added = 0
    print(f"[NewsIndexer] 开始批量入库，目标：{len(stock_list)} 只股票")

    for stock_code, stock_name in stock_list:
        try:
            raw = _fetch_news(stock_code)
            if not raw:
                print(f"   ⚠️ {stock_name} 无新闻，跳过")
                continue
            items = _parse_news(raw, stock_code, stock_name)[:limit_per_stock]
            added = _insert_news_batch(items)
            total_added += added
            print(f"   ✅ {stock_name}：新增 {added} 条")
        except Exception as e:
            print(f"   ❌ {stock_name} 入库失败: {e}")
        time.sleep(0.5)

    print(f"[NewsIndexer] 批量入库完成，新增 {total_added} 条")
    return total_added


# ── 功能二：流式更新 ──────────────────────────────────────────────────

_stream_running = False
_stream_thread: threading.Thread | None = None


def stream_update_once(stock_list: list[tuple] = None) -> int:
    if stock_list is None:
        stock_list = WATCH_LIST

    added_total = 0
    for stock_code, stock_name in stock_list:
        try:
            raw = _fetch_news(stock_code)
            if not raw:
                continue
            items = _parse_news(raw, stock_code, stock_name)
            added_total += _insert_news_batch(items)
        except Exception as e:
            print(f"[Stream] {stock_name} 更新失败: {e}")

    if added_total:
        print(f"[Stream] {datetime.now().strftime('%H:%M:%S')} 新增 {added_total} 条")
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
            SELECT title, stock_name, pub_time
            FROM news_vectors
            WHERE stock_code = %s AND date >= %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        params = (stock_code, cutoff, str(query_emb), k)
    else:
        sql = """
            SELECT title, stock_name, pub_time
            FROM news_vectors
            WHERE date >= %s
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

    return "\n".join([f"【{r[1]} | {r[2]}】{r[0]}" for r in rows])


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
            cur.execute("SELECT COUNT(*) FROM news_vectors")
            total = cur.fetchone()[0]
    return {
        "total_news": total,
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
