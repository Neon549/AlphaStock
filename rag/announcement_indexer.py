"""Low-noise CNInfo announcement ingestion for the stock RAG corpus.

The news feed is good at market reactions and fund-flow commentary, but it can
miss the primary evidence for dividends, earnings, buybacks and management
changes.  This module adds official disclosure chunks to the existing
``news_vectors`` table while keeping provenance explicit and download volume
bounded.
"""

from __future__ import annotations

import html
import math
import re
import time
from datetime import date, datetime, timedelta
from typing import Any

import fitz
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from db import get_conn
from rag.news_indexer import _insert_news_batch


CNINFO_QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STOCK_MAP_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_PDF_BASE = "https://static.cninfo.com.cn/"
PUBLISHER = "巨潮资讯"

# Terms are intentionally tied to factual questions the primary source can
# answer.  Generic meeting notices and legal opinions are left out to protect
# Context Precision.
HIGH_VALUE_TERMS = (
    "业绩预告",
    "业绩快报",
    "年度报告摘要",
    "半年度报告摘要",
    "季度报告",
    "利润分配",
    "权益分派",
    "分红",
    "派息",
    "回购",
    "增持",
    "减持",
    "辞职",
    "离任",
    "任职资格",
    "聘任",
    "高级管理人员",
    "董事会秘书",
    "公司秘书",
    "对外投资",
    "成立子公司",
    "设立子公司",
    "日常经营",
    "重大合同",
    "中标",
    "订单",
    "合作",
    "主营业务",
    "重大事项",
    "挂牌并上市",
    "境外上市",
    "H股发行",
)

EXCLUDED_TITLE_TERMS = (
    "法律意见",
    "核查意见",
    "募集资金存放",
    "内部控制",
    "管理制度",
    "工作细则",
    "会议通知",
    "月报表",
    "英文版",
)

CHUNK_SIGNAL_TERMS = (
    "营业收入",
    "净利润",
    "同比",
    "每10股",
    "现金红利",
    "分红",
    "回购金额",
    "回购价格",
    "累计回购",
    "聘任",
    "辞职",
    "离任",
    "接任",
    "注册资本",
    "子公司",
    "合同金额",
    "中标金额",
    "上市日期",
    "联交所",
)


def _session() -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=None,
    )
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 AlphaStock-RAG/1.0",
            "Accept": "application/json,text/plain,*/*",
        }
    )
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def ensure_evidence_schema() -> None:
    """Apply only additive columns/indexes required for source provenance."""

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE news_vectors ADD COLUMN IF NOT EXISTS source_kind TEXT NOT NULL DEFAULT 'news'")
            cur.execute("ALTER TABLE news_vectors ADD COLUMN IF NOT EXISTS source_url TEXT")
            cur.execute("ALTER TABLE news_vectors ADD COLUMN IF NOT EXISTS publisher TEXT")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_nv_stock_source_date "
                "ON news_vectors(stock_code, source_kind, date DESC)"
            )
        conn.commit()


def _clean_title(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_stock_map(session: requests.Session) -> dict[str, str]:
    response = session.get(CNINFO_STOCK_MAP_URL, timeout=(10, 30))
    response.raise_for_status()
    return {
        str(item.get("code")): str(item.get("orgId"))
        for item in response.json().get("stockList", [])
    }


def announcement_priority(title: str) -> int:
    """Return a positive score only for disclosures likely to contain facts."""

    normalized = _clean_title(title)
    if any(term in normalized for term in EXCLUDED_TITLE_TERMS):
        return 0
    hits = sum(1 for term in HIGH_VALUE_TERMS if term.lower() in normalized.lower())
    if hits == 0:
        return 0
    score = hits * 10
    if "摘要" in normalized or "业绩预告" in normalized or "权益分派" in normalized:
        score += 8
    if "进展" in normalized or "实施" in normalized:
        score += 3
    return score


def fetch_announcements(
    stock_code: str,
    *,
    start_date: date,
    end_date: date,
    max_documents: int = 16,
    session: requests.Session | None = None,
    org_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch high-value announcement metadata for one A-share stock."""

    own_session = session is None
    client = session or _session()
    try:
        resolved_org_map = org_map or _fetch_stock_map(client)
        org_id = resolved_org_map.get(str(stock_code))
        if not org_id:
            raise KeyError(f"CNInfo stock mapping missing: {stock_code}")

        payload = {
            "pageNum": "1",
            "pageSize": "30",
            "column": "szse",
            "tabName": "fulltext",
            "plate": "",
            "stock": f"{stock_code},{org_id}",
            "searchkey": "",
            "secid": "",
            "category": "",
            "trade": "",
            "seDate": f"{start_date:%Y-%m-%d}~{end_date:%Y-%m-%d}",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "false",
        }
        response = client.post(CNINFO_QUERY_URL, data=payload, timeout=(10, 45))
        response.raise_for_status()
        first = response.json()
        total = int(first.get("totalAnnouncement") or 0)
        pages = max(1, math.ceil(total / 30))
        raw_items = list(first.get("announcements") or [])
        for page in range(2, pages + 1):
            payload["pageNum"] = str(page)
            response = client.post(CNINFO_QUERY_URL, data=payload, timeout=(10, 45))
            response.raise_for_status()
            raw_items.extend(response.json().get("announcements") or [])

        selected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in raw_items:
            announcement_id = str(item.get("announcementId") or "").strip()
            title = _clean_title(item.get("announcementTitle"))
            adjunct_url = str(item.get("adjunctUrl") or "").lstrip("/")
            priority = announcement_priority(title)
            if not announcement_id or announcement_id in seen_ids or priority <= 0:
                continue
            seen_ids.add(announcement_id)
            timestamp = item.get("announcementTime")
            published = datetime.fromtimestamp(float(timestamp) / 1000).strftime("%Y-%m-%d")
            selected.append(
                {
                    "announcement_id": announcement_id,
                    "stock_code": str(stock_code),
                    "stock_name": str(item.get("secName") or stock_code),
                    "title": title,
                    "pub_time": published,
                    "date": published,
                    "source_url": f"{CNINFO_PDF_BASE}{adjunct_url}" if adjunct_url else "",
                    "priority": priority,
                }
            )
        selected.sort(key=lambda item: (item["priority"], item["date"]), reverse=True)
        return selected[: max(1, max_documents)]
    finally:
        if own_session:
            client.close()


def _download_pdf(
    url: str,
    *,
    session: requests.Session,
    max_bytes: int = 20 * 1024 * 1024,
) -> bytes:
    if not url:
        return b""
    response = session.get(url, stream=True, timeout=(10, 60))
    response.raise_for_status()
    content_length = int(response.headers.get("Content-Length") or 0)
    if content_length > max_bytes:
        return b""
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(chunk_size=128 * 1024):
        if not chunk:
            continue
        size += len(chunk)
        if size > max_bytes:
            return b""
        chunks.append(chunk)
    return b"".join(chunks)


def _pdf_text(pdf_bytes: bytes, max_pages: int = 30) -> str:
    if not pdf_bytes:
        return ""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
        pages = [document[index].get_text("text") for index in range(min(len(document), max_pages))]
    text = "\n".join(pages)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def select_evidence_chunks(
    text: str,
    *,
    chunk_chars: int = 900,
    overlap_chars: int = 120,
    max_chunks: int = 6,
) -> list[str]:
    """Chunk extracted PDF text and retain first/high-signal factual regions."""

    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(compact):
        end = min(len(compact), start + chunk_chars)
        chunk = compact[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(compact):
            break
        start = max(start + 1, end - overlap_chars)

    def score(index: int) -> tuple[float, int]:
        chunk = chunks[index]
        signal_hits = sum(chunk.count(term) for term in CHUNK_SIGNAL_TERMS)
        number_hits = min(8, len(re.findall(r"\d+(?:\.\d+)?(?:%|亿元|万元|元|股)?", chunk)))
        first_page_bonus = 6 if index < 2 else 0
        return signal_hits * 5 + number_hits + first_page_bonus, -index

    ranked_indices = sorted(range(len(chunks)), key=score, reverse=True)
    selected = sorted(ranked_indices[: max(1, max_chunks)])
    return [chunks[index] for index in selected]


def build_announcement_items(
    metadata: dict[str, Any],
    pdf_bytes: bytes,
    *,
    max_chunks: int = 6,
) -> list[dict[str, Any]]:
    """Convert one official disclosure into independently retrievable chunks."""

    chunks = select_evidence_chunks(_pdf_text(pdf_bytes), max_chunks=max_chunks)
    if not chunks:
        chunks = [""]
    output: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        full_text = (
            f"{metadata['stock_name']}（{metadata['stock_code']}） "
            f"{metadata['pub_time']} 公告：{metadata['title']}"
        )
        if chunk:
            full_text += f" 内容：{chunk}"
        full_text += f" 来源：{PUBLISHER} 链接：{metadata['source_url']}"
        output.append(
            {
                "id": f"cninfo:{metadata['announcement_id']}:{index}",
                "stock_code": metadata["stock_code"],
                "stock_name": metadata["stock_name"],
                "title": metadata["title"],
                "full_text": full_text,
                "pub_time": metadata["pub_time"],
                "date": metadata["date"],
                "source_kind": "announcement",
                "source_url": metadata["source_url"],
                "publisher": PUBLISHER,
            }
        )
    return output


def bulk_index_announcements(
    stock_list: list[tuple[str, str]],
    *,
    lookback_days: int = 30,
    max_documents_per_stock: int = 16,
    max_chunks_per_document: int = 6,
) -> dict[str, int]:
    """Fetch, extract and upsert official disclosure evidence."""

    ensure_evidence_schema()
    client = _session()
    start = date.today() - timedelta(days=max(1, lookback_days))
    end = date.today()
    totals = {"documents": 0, "chunks": 0, "added": 0, "updated": 0, "embedded": 0, "failed": 0}
    try:
        org_map = _fetch_stock_map(client)
        for stock_code, fallback_name in stock_list:
            try:
                metadata_rows = fetch_announcements(
                    stock_code,
                    start_date=start,
                    end_date=end,
                    max_documents=max_documents_per_stock,
                    session=client,
                    org_map=org_map,
                )
                stock_items: list[dict[str, Any]] = []
                for metadata in metadata_rows:
                    metadata["stock_name"] = metadata.get("stock_name") or fallback_name
                    try:
                        pdf_bytes = _download_pdf(metadata["source_url"], session=client)
                        stock_items.extend(
                            build_announcement_items(
                                metadata,
                                pdf_bytes,
                                max_chunks=max_chunks_per_document,
                            )
                        )
                    except Exception as exc:
                        print(f"   ⚠️ {stock_code} 公告正文失败，保留标题: {metadata['title']} ({exc})")
                        stock_items.extend(build_announcement_items(metadata, b"", max_chunks=1))
                        totals["failed"] += 1
                    time.sleep(0.1)
                stats = _insert_news_batch(stock_items, return_stats=True)
                totals["documents"] += len(metadata_rows)
                totals["chunks"] += len(stock_items)
                totals["added"] += stats["added"]
                totals["updated"] += stats["updated"]
                totals["embedded"] += stats["embedded"]
                print(
                    f"   ✅ {fallback_name}：公告 {len(metadata_rows)} 份，"
                    f"证据块 {len(stock_items)}，新增 {stats['added']}"
                )
            except Exception as exc:
                totals["failed"] += 1
                print(f"   ❌ {fallback_name} 公告入库失败: {exc}")
    finally:
        client.close()
    return totals
