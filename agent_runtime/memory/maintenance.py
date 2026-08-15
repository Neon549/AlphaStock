"""Durable, least-privilege background extraction and sleep consolidation.

The worker is a logical fork: it receives a bounded persisted transcript range,
not a live request object or an assumption about provider prompt-cache sharing.
It can create *pending* candidates only; approval/indexing remain human actions.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agent_runtime.memory.taxonomy import is_allowed_scope
from config.runtime_paths import REPORTS_DIR, ensure_runtime_dirs


MIN_NEW_TURNS = 8
MIN_NEW_CHARS = 20_000  # conservative proxy until provider token usage is persisted
MAX_SOURCE_CHARS = 18_000
MAX_CANDIDATES_PER_JOB = 3


def enqueue_extraction_if_due(session_id: str | None, actor_id: str | None) -> str | None:
    """Queue one extraction job after enough new authenticated conversation."""

    if not session_id or not actor_id:
        return None
    from db import get_conn
    from psycopg2.extras import Json

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT memory FROM agent_session_memory WHERE session_id = %s", (session_id,))
            row = cur.fetchone()
            memory = (row[0] if row else {}) or {}
            watermark = int(((memory.get("maintenance") or {}).get("last_extracted_transcript_id") or 0))
            cur.execute(
                """SELECT COALESCE(MIN(id), 0), COALESCE(MAX(id), 0), COUNT(*), COALESCE(SUM(length(content)), 0)
                   FROM agent_session_transcript WHERE session_id = %s AND id > %s""",
                (session_id, watermark),
            )
            start_id, end_id, turns, chars = cur.fetchone()
            if not end_id or (turns < MIN_NEW_TURNS and chars < MIN_NEW_CHARS):
                return None
            job_id = str(uuid.uuid4())
            cur.execute(
                """INSERT INTO agent_memory_maintenance_jobs
                   (job_id, kind, status, session_id, actor_id, source_from_id, source_to_id, payload)
                   VALUES (%s, 'extract', 'queued', %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (job_id, session_id, actor_id, int(start_id), int(end_id), Json({"turns": turns, "chars": chars})),
            )
            if cur.rowcount == 0:
                return None
        conn.commit()
    return job_id


def _parse_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return {"candidates": []}
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"candidates": []}
    return value if isinstance(value, dict) else {"candidates": []}


def extract_candidate_drafts(transcript: list[dict[str, Any]], llm: Any) -> list[dict[str, str]]:
    """Ask a worker model for conservative draft lessons; no tool access exists."""

    source = "\n".join(f"[{row['id']}] {row['role']}: {row['content']}" for row in transcript)
    source = source[-MAX_SOURCE_CHARS:]
    prompt = """You are a background memory extractor for an investment research system.
Create at most 3 reusable OPERATING lessons from the transcript. Do not store stock facts, prices,
financial numbers, recommendations, personal information, secrets, or any promise of return.
Only emit a lesson when a repeated, concrete workflow/governance/retrieval failure or verified procedure exists.
Allowed categories: governance, research, retrieval, workflow, operations, backtest, evaluation.
Return JSON only: {"candidates":[{"title":"...","category":"...","content":"..."}]}.
Each content must state the observed pattern and a repeatable action. Empty candidates is valid.

Transcript:\n""" + source
    response = llm.invoke(prompt)
    raw = getattr(response, "content", response)
    parsed = _parse_json(str(raw))
    output: list[dict[str, str]] = []
    for item in parsed.get("candidates", [])[:MAX_CANDIDATES_PER_JOB]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:120]
        category = str(item.get("category") or "").strip().lower()
        content = str(item.get("content") or "").strip()[:12_000]
        if title and is_allowed_scope(category) and len(content) >= 30:
            output.append({"title": title, "category": category, "content": content})
    return output


def _claim_job(kind: str) -> dict[str, Any] | None:
    from db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT job_id, session_id, actor_id, source_from_id, source_to_id, payload
                   FROM agent_memory_maintenance_jobs WHERE kind = %s AND status = 'queued'
                   ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""",
                (kind,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cur.execute("UPDATE agent_memory_maintenance_jobs SET status='running', started_at=NOW() WHERE job_id=%s", (row[0],))
        conn.commit()
    keys = ("job_id", "session_id", "actor_id", "source_from_id", "source_to_id", "payload")
    return dict(zip(keys, row))


def _finish_job(job_id: str, *, status: str, payload: dict[str, Any] | None = None, error: str | None = None) -> None:
    from db import get_conn
    from psycopg2.extras import Json
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_memory_maintenance_jobs SET status=%s, payload=%s, error=%s, completed_at=NOW() WHERE job_id=%s",
                (status, Json(payload or {}), (error or "")[:1_000] or None, job_id),
            )
        conn.commit()


def _advance_watermark(session_id: str, transcript_id: int) -> None:
    from db import get_conn
    from psycopg2.extras import Json
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT memory FROM agent_session_memory WHERE session_id=%s FOR UPDATE", (session_id,))
            row = cur.fetchone()
            memory = (row[0] if row else {}) or {}
            maintenance = memory.setdefault("maintenance", {})
            maintenance.update({"last_extracted_transcript_id": transcript_id, "last_extracted_at": datetime.now(timezone.utc).isoformat()})
            cur.execute("UPDATE agent_session_memory SET memory=%s, updated_at=NOW() WHERE session_id=%s", (Json(memory), session_id))
        conn.commit()


def run_one_extraction_job(llm: Any | None = None) -> dict[str, Any] | None:
    """Run exactly one queued job; intended for a scheduler/worker process."""

    job = _claim_job("extract")
    if not job:
        return None
    try:
        from db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, role, content, run_id FROM agent_session_transcript
                       WHERE session_id=%s AND id BETWEEN %s AND %s ORDER BY id""",
                    (job["session_id"], job["source_from_id"], job["source_to_id"]),
                )
                rows = cur.fetchall()
        transcript = [{"id": row[0], "role": row[1], "content": row[2], "run_id": row[3]} for row in rows]
        if not transcript:
            _finish_job(job["job_id"], status="skipped", payload={"reason": "empty_transcript"})
            return {"job_id": job["job_id"], "status": "skipped"}
        if llm is None:
            from config.llm_config import quick_llm
            llm = quick_llm
        from agent_runtime.memory.candidates import create_candidate
        drafts = extract_candidate_drafts(transcript, llm)
        candidate_ids = [
            create_candidate(
                title=draft["title"], content=draft["content"], category=draft["category"],
                source_run_id=transcript[-1].get("run_id"), requested_by=job["actor_id"],
            ).candidate_id
            for draft in drafts
        ]
        from agent_runtime.memory.candidates import get_candidate
        resolved_candidates = [get_candidate(candidate_id) for candidate_id in candidate_ids]
        auto_approved_ids = [
            candidate_id for candidate_id, candidate in zip(candidate_ids, resolved_candidates)
            if candidate and candidate.get("status") == "approved"
        ]
        _advance_watermark(job["session_id"], int(job["source_to_id"]))
        payload = {
            "candidate_ids": candidate_ids,
            "source_turns": len(transcript),
            "auto_approved": bool(auto_approved_ids),
            "auto_approved_ids": auto_approved_ids,
            "index_sync_required": bool(auto_approved_ids),
        }
        _finish_job(job["job_id"], status="completed", payload=payload)
        return {"job_id": job["job_id"], "status": "completed", **payload}
    except Exception as exc:
        _finish_job(job["job_id"], status="failed", error=str(exc))
        raise


def write_sleep_consolidation_report() -> Path:
    """Safe 'sleep' stage: report possible duplicate lessons, never edit them."""

    from agent_runtime.memory.index import approved_memory_files
    files = approved_memory_files()
    titles: dict[str, list[str]] = {}
    for path in files:
        heading = next((line[2:].strip() for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("# ")), path.stem)
        key = re.sub(r"\W+", " ", heading.lower()).strip()
        titles.setdefault(key, []).append(str(path))
    duplicates = [paths for paths in titles.values() if len(paths) > 1]
    ensure_runtime_dirs()
    output = REPORTS_DIR / f"memory-sleep-{datetime.now().strftime('%Y%m%d')}.json"
    output.write_text(json.dumps({"approved_files": len(files), "exact_title_duplicates": duplicates, "auto_modified": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    return output
