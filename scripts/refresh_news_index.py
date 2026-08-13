"""Incrementally refresh the PostgreSQL news index through a configured DB route.

The script is intentionally separate from application startup.  It can be run
on the Tencent Cloud host, or locally through an SSH tunnel, and writes only
new Eastmoney news rows.  Existing rows are protected by ``news_vectors.id``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.pool
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag.news_indexer import EVAL_STOCKS, WATCH_LIST, bulk_index, get_stats


def _remote_dsn(port: int) -> str:
    """Build a tunnel DSN without printing or persisting the password."""

    config = dotenv_values(ROOT / ".env")
    # ``rag.news_indexer`` imports ``db`` before this function runs, and
    # ``db.py`` may load the local .env.pgvector override into os.environ.
    # The project .env is the explicit source for the tunnel credentials here.
    raw_dsn = config.get("POSTGRES_DSN") or os.getenv("POSTGRES_DSN")
    if not raw_dsn:
        raise RuntimeError("POSTGRES_DSN is required")
    info = psycopg2.extensions.parse_dsn(raw_dsn)
    return (
        f"host=127.0.0.1 port={port} dbname={info.get('dbname', '')} "
        f"user={info.get('user', '')} password={info.get('password', '')}"
    )


def _stock_list(args: argparse.Namespace) -> list[tuple[str, str]]:
    by_code = dict(WATCH_LIST)
    by_code.update(dict(EVAL_STOCKS))
    if args.stocks:
        selected = []
        for code in args.stocks.split(","):
            code = code.strip()
            if not code:
                continue
            if code.isdigit():
                code = code.zfill(6)
            selected.append((code, by_code.get(code, code)))
        return selected
    if args.all_existing:
        with psycopg2.connect(_remote_dsn(args.port), connect_timeout=8) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT stock_code, stock_name FROM news_vectors ORDER BY stock_code")
                existing = []
                for code, name in cur.fetchall():
                    normalized = str(code).strip()
                    if normalized.isdigit():
                        normalized = normalized.zfill(6)
                    existing.append((normalized, str(name)))
        merged = dict(existing)
        merged.update(by_code)
        return sorted(merged.items())
    return WATCH_LIST


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.getenv("RAG_NEWS_DB_PORT", "15432")))
    parser.add_argument("--stocks", help="comma-separated stock codes; defaults to WATCH_LIST")
    parser.add_argument("--all-existing", action="store_true", help="also refresh every stock already present in news_vectors")
    parser.add_argument("--limit-per-stock", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stocks = _stock_list(args)
    if args.dry_run:
        print(json.dumps({"event": "dry_run", "stock_codes": [code for code, _ in stocks]}, ensure_ascii=False), flush=True)
        return 0

    dsn = _remote_dsn(args.port)
    import db

    db.POSTGRES_DSN = dsn
    # A single forwarded PostgreSQL connection is much more reliable over a
    # consumer SSH tunnel than the application's eager two-connection pool.
    # This pool is process-local and does not change production service limits.
    db._pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=1,
        dsn=dsn,
        connect_timeout=20,
    )
    try:
        before = get_stats()
        started_at = datetime.now(timezone.utc).isoformat()
        print(json.dumps({"event": "refresh_started", "started_at": started_at, "stocks": len(stocks), "before": before}, ensure_ascii=False), flush=True)

        added = bulk_index(stocks, limit_per_stock=max(1, args.limit_per_stock))
        after = get_stats()
        summary = {
            "event": "refresh_finished",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "stocks": len(stocks),
            "added_reported": added,
            "before": before,
            "after": after,
            "delta_total_news": after.get("total_news", 0) - before.get("total_news", 0),
        }
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    finally:
        db._pool.closeall()
        db._pool = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
