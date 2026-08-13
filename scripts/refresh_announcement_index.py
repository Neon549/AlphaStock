"""Incrementally add official CNInfo disclosures to the remote RAG index."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2.pool

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag.announcement_indexer import bulk_index_announcements
from rag.news_indexer import EVAL_STOCKS, WATCH_LIST, get_stats
from scripts.refresh_news_index import _remote_dsn


def _stocks(value: str | None, all_watch: bool) -> list[tuple[str, str]]:
    known = dict(WATCH_LIST)
    if value:
        codes = [part.strip().zfill(6) for part in value.split(",") if part.strip()]
        return [(code, known.get(code, code)) for code in codes]
    return WATCH_LIST if all_watch else EVAL_STOCKS


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.getenv("RAG_NEWS_DB_PORT", "15432")))
    parser.add_argument("--stocks", help="comma-separated stock codes; defaults to EVAL_STOCKS")
    parser.add_argument("--all-watch", action="store_true", help="refresh the complete production watch list")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--max-documents-per-stock", type=int, default=16)
    parser.add_argument("--max-chunks-per-document", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    stocks = _stocks(args.stocks, args.all_watch)
    if args.dry_run:
        print(json.dumps({"event": "dry_run", "stocks": stocks}, ensure_ascii=False))
        return 0

    dsn = _remote_dsn(args.port)
    import db

    db.POSTGRES_DSN = dsn
    db._pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=1,
        dsn=dsn,
        connect_timeout=20,
    )
    try:
        before = get_stats()
        print(
            json.dumps(
                {
                    "event": "announcement_refresh_started",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "stocks": len(stocks),
                    "before": before,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        result = bulk_index_announcements(
            stocks,
            lookback_days=args.lookback_days,
            max_documents_per_stock=args.max_documents_per_stock,
            max_chunks_per_document=args.max_chunks_per_document,
        )
        after = get_stats()
        print(
            json.dumps(
                {
                    "event": "announcement_refresh_finished",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "result": result,
                    "after": after,
                    "delta_total_news": after.get("total_news", 0) - before.get("total_news", 0),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        db._pool.closeall()
        db._pool = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
