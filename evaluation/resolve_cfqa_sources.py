"""Resolve CFQA annual-report pages to official CNINFO PDF sources.

The resolver only records official CNINFO links.  It does not download PDFs or
promote labels to Gold; the returned rows remain pending independent review.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests


CNINFO = "https://www.cninfo.com.cn"
STOCK_LIST_URL = f"{CNINFO}/new/data/szse_stock.json"
ANNOUNCEMENT_URL = f"{CNINFO}/new/hisAnnouncement/query"
PDF_PREFIX = "https://static.cninfo.com.cn/"
TAG_RE = re.compile(r"<[^>]+>")
YEAR_RE = re.compile(r"(?:19|20)\d{2}")
SHORT_YEAR_RE = re.compile(r"(?<!\d)(\d{2})年")


def _headers() -> dict[str, str]:
    return {
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": CNINFO,
        "Referer": f"{CNINFO}/new/commonUrl/pageOfSearch?url=disclosure/list/search",
        "User-Agent": "AlphaStock-CFQA-Resolver/1.0",
        "X-Requested-With": "XMLHttpRequest",
    }


def _clean_title(value: Any) -> str:
    text = html.unescape(TAG_RE.sub("", str(value or "")))
    return re.sub(r"\s+", "", text)


def _code(value: Any) -> str:
    return str(value or "").strip().zfill(6)


def _year(row: dict[str, Any]) -> int | None:
    for field in ("query", "reference_answer"):
        text = str(row.get(field, ""))
        match = YEAR_RE.search(text)
        if match:
            return int(match.group())
        short_match = SHORT_YEAR_RE.search(text)
        if short_match:
            short_year = int(short_match.group(1))
            return 2000 + short_year if short_year <= 30 else 1900 + short_year
    return None


def load_org_ids(session: requests.Session) -> dict[str, str]:
    response = session.get(STOCK_LIST_URL, timeout=(10, 30))
    response.raise_for_status()
    payload = response.json()
    return {
        _code(item.get("code")): str(item.get("orgId", ""))
        for item in payload.get("stockList", [])
        if item.get("code") and item.get("orgId")
    }


def query_reports(
    session: requests.Session,
    *,
    code: str,
    org_id: str,
    year: int,
    retries: int = 2,
) -> list[dict[str, Any]]:
    is_shanghai = code.startswith(("6", "9"))
    params = {
        "pageNum": "1",
        "pageSize": "30",
        "column": "sse" if is_shanghai else "szse",
        "tabName": "fulltext",
        "plate": "sh" if is_shanghai else "sz",
        "stock": f"{code},{org_id}",
        "searchkey": f"{year}年年度报告",
        "secid": "",
        "category": "category_ndbg_szsh;",
        "trade": "",
        "seDate": f"{year + 1}-01-01~{year + 2}-06-30",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.post(ANNOUNCEMENT_URL, data=params, timeout=(10, 30))
            response.raise_for_status()
            payload = response.json()
            return list(payload.get("announcements") or [])
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"CNINFO query failed for {code}/{year}: {last_error}")


def choose_full_report(announcements: list[dict[str, Any]], year: int) -> dict[str, Any] | None:
    expected = f"{year}年年度报告"
    candidates = []
    for announcement in announcements:
        title = _clean_title(announcement.get("announcementTitle"))
        adjunct = str(announcement.get("adjunctUrl") or "").strip()
        if expected not in title or "摘要" in title or not adjunct.lower().endswith(".pdf"):
            continue
        # Prefer the original annual report over a later revised copy.
        revised = "修订" in title or "更正" in title
        timestamp = int(announcement.get("announcementTime") or 0)
        candidates.append((revised, timestamp, title, adjunct, announcement))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    _, timestamp, title, adjunct, announcement = candidates[0]
    published_at = datetime.fromtimestamp(timestamp / 1000, tz=UTC).date().isoformat() if timestamp else ""
    return {
        "title": title,
        "published_at": published_at,
        "source_url": f"{PDF_PREFIX}{adjunct.lstrip('/')}",
        "announcement_id": announcement.get("announcementId"),
    }


def resolve(
    rows: list[dict[str, Any]],
    *,
    delay_seconds: float = 0.25,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session = requests.Session()
    session.headers.update(_headers())
    org_ids = load_org_ids(session)
    output: list[dict[str, Any]] = []
    documents: dict[str, dict[str, Any]] = {}
    selected = rows[:limit] if limit else rows
    for index, row in enumerate(selected):
        code = _code(row.get("stock_code"))
        year = _year(row)
        resolved: dict[str, Any] | None = None
        error: dict[str, Any] | None = None
        if not year:
            error = {"reason": "query_year_not_found"}
        elif code not in org_ids:
            error = {"reason": "cninfo_org_id_not_found", "security_code": code, "query_year": year}
        else:
            try:
                announcements = query_reports(session, code=code, org_id=org_ids[code], year=year)
                resolved = choose_full_report(announcements, year)
                if resolved is None:
                    error = {
                        "reason": "matching_annual_report_not_found",
                        "security_code": code,
                        "query_year": year,
                        "announcement_count": len(announcements),
                    }
            except RuntimeError as exc:
                error = {"reason": "cninfo_query_error", "message": str(exc)}
        item = {**row, "status": "source_resolved" if resolved else "source_resolution_pending"}
        if resolved:
            document_id = f"{code}-{year}-annual"
            item["source"] = {
                "document_id": document_id,
                "security_code": code,
                "company": row.get("company") or row.get("source_company"),
                "report_period": f"FY{year}",
                "published_at": resolved["published_at"],
                "title": resolved["title"],
                "source_host": "cninfo",
                "source_url": resolved["source_url"],
                "announcement_id": resolved.get("announcement_id"),
            }
            documents[document_id] = item["source"]
        else:
            item["resolution_error"] = error
        output.append(item)
        if index + 1 < len(selected):
            time.sleep(delay_seconds)
    summary = {
        "input_count": len(selected),
        "resolved_count": sum(row["status"] == "source_resolved" for row in output),
        "pending_count": sum(row["status"] != "source_resolved" for row in output),
        "document_count": len(documents),
        "documents": list(documents.values()),
        "source_policy": "Official CNINFO annual-report PDFs only; independently review before Gold promotion.",
    }
    return output, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve CFQA candidates to official CNINFO annual-report PDFs")
    parser.add_argument("--input", type=Path, required=True, help="CFQA mapping candidate JSONL")
    parser.add_argument("--output", type=Path, required=True, help="Resolved candidate JSONL")
    parser.add_argument("--sources-out", type=Path, required=True, help="Source manifest JSON")
    parser.add_argument("--delay-seconds", type=float, default=0.25)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    resolved, summary = resolve(rows, delay_seconds=max(0.0, args.delay_seconds), limit=args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in resolved), encoding="utf-8")
    args.sources_out.parent.mkdir(parents=True, exist_ok=True)
    args.sources_out.write_text(
        json.dumps(
            {
                "dataset_id": "external-cfqa-source-resolution-v2",
                "purpose": "Official CNINFO annual-report sources resolved from CFQA page-anchored candidates.",
                **summary,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in summary.items() if key != "documents"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
