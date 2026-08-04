"""Human-review boundary for reusable Agent experience.

Candidates are intentionally not indexed and do not enter any model context.
Only an explicit approval turns one into a versioned Markdown source file; the
normal memory-index sync then derives vectors from that file.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runtime.memory.index import MEMORY_KNOWLEDGE_DIR
from agent_runtime.memory.taxonomy import FORBIDDEN_MEMORY_CONTENT_HINTS, is_allowed_scope


_CATEGORY = re.compile(r"^[a-z0-9_-]{1,40}$")
_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class MemoryCandidate:
    candidate_id: str
    title: str
    content: str
    category: str = "governance"
    source_run_id: str | None = None
    requested_by: str | None = None


def _validate(candidate: MemoryCandidate) -> None:
    if not candidate.title.strip() or len(candidate.title) > 120:
        raise ValueError("title must contain 1-120 characters")
    if len(candidate.content.strip()) < 30 or len(candidate.content) > 12_000:
        raise ValueError("content must contain 30-12000 characters")
    if not _CATEGORY.fullmatch(candidate.category) or not is_allowed_scope(candidate.category):
        raise ValueError("category must be one of the approved memory taxonomy scopes")
    lowered = candidate.content.lower()
    if any(hint.lower() in lowered for hint in FORBIDDEN_MEMORY_CONTENT_HINTS):
        raise ValueError("candidate appears to contain live market claims or investment promises")


def create_candidate(
    *, title: str, content: str, category: str = "governance",
    source_run_id: str | None = None, requested_by: str | None = None,
) -> MemoryCandidate:
    """Store a reviewable candidate; it cannot be retrieved at this stage."""
    candidate = MemoryCandidate(
        candidate_id=str(uuid.uuid4()), title=title.strip(), content=content.strip(),
        category=category.strip().lower(), source_run_id=source_run_id, requested_by=requested_by,
    )
    _validate(candidate)
    from db import execute

    execute(
        """
        INSERT INTO agent_memory_candidates
            (candidate_id, status, title, category, content, source_run_id, requested_by)
        VALUES (%s, 'pending', %s, %s, %s, %s, %s)
        """,
        (
            candidate.candidate_id, candidate.title, candidate.category, candidate.content,
            candidate.source_run_id, candidate.requested_by,
        ),
    )
    return candidate


def get_candidate(candidate_id: str) -> dict[str, Any] | None:
    from db import execute

    row = execute(
        """
        SELECT candidate_id, status, title, category, content, source_run_id, requested_by,
               reviewer, review_note, approved_path, created_at, reviewed_at
        FROM agent_memory_candidates WHERE candidate_id = %s
        """,
        (candidate_id,), fetch="one",
    )
    if not row:
        return None
    keys = (
        "candidate_id", "status", "title", "category", "content", "source_run_id",
        "requested_by", "reviewer", "review_note", "approved_path", "created_at", "reviewed_at",
    )
    return dict(zip(keys, row))


def list_candidates(*, requested_by: str, status: str = "pending", limit: int = 50) -> list[dict[str, Any]]:
    """List only one user's candidate queue; content is returned for review."""
    if status not in {"pending", "approved", "rejected"}:
        raise ValueError("status must be pending, approved or rejected")
    from db import execute

    rows = execute(
        """
        SELECT candidate_id, status, title, category, content, source_run_id, requested_by,
               reviewer, review_note, approved_path, created_at, reviewed_at
        FROM agent_memory_candidates
        WHERE requested_by = %s AND status = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (requested_by, status, max(1, min(limit, 100))), fetch="all",
    ) or []
    keys = (
        "candidate_id", "status", "title", "category", "content", "source_run_id",
        "requested_by", "reviewer", "review_note", "approved_path", "created_at", "reviewed_at",
    )
    return [dict(zip(keys, row)) for row in rows]


def _filename(candidate: dict[str, Any]) -> str:
    ascii_title = _SLUG.sub("-", candidate["title"].lower()).strip("-") or "experience"
    return f"{candidate['candidate_id'][:8]}-{ascii_title[:48]}.md"


def render_approved_markdown(candidate: dict[str, Any], reviewer: str) -> str:
    """Render a deterministic, review-attributed source document."""
    return (
        "---\n"
        "status: approved\n"
        f"scope: {candidate['category']}\n"
        "evidence_class: operating_knowledge\n"
        "market_fact_policy: never_override_current_evidence\n"
        f"owner: {reviewer}\n"
        "version: 1.0.0\n"
        f"source_candidate: {candidate['candidate_id']}\n"
        "---\n\n"
        f"# {candidate['title']}\n\n"
        f"{candidate['content'].strip()}\n"
    )


def review_candidate(
    candidate_id: str, *, approved: bool, reviewer: str, review_note: str = "",
    root: Path = MEMORY_KNOWLEDGE_DIR,
) -> dict[str, Any] | None:
    """Resolve once. Approval writes a Markdown source but does not auto-index it."""
    candidate = get_candidate(candidate_id)
    if not candidate or candidate["status"] != "pending":
        return None
    if not reviewer.strip():
        raise ValueError("reviewer is required")

    approved_path: str | None = None
    target: Path | None = None
    temporary: Path | None = None
    if approved:
        target = root / candidate["category"] / _filename(candidate)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".md.pending")
        temporary.write_text(render_approved_markdown(candidate, reviewer.strip()), encoding="utf-8")
        approved_path = target.relative_to(root).as_posix()

    from db import execute

    status = "approved" if approved else "rejected"
    try:
        changed = execute(
            """
            UPDATE agent_memory_candidates
            SET status = %s, reviewer = %s, review_note = %s, approved_path = %s, reviewed_at = NOW()
            WHERE candidate_id = %s AND status = 'pending'
            """,
            (status, reviewer.strip(), review_note.strip()[:1_000], approved_path, candidate_id),
        )
        # ``execute`` intentionally has no rowcount contract. Re-read below
        # to determine whether another reviewer resolved the candidate first.
        resolved = get_candidate(candidate_id)
        if not resolved or resolved["status"] != status:
            if temporary and temporary.exists():
                temporary.unlink()
            return None
        if temporary and target:
            temporary.replace(target)
        return resolved
    except Exception:
        if temporary and temporary.exists():
            temporary.unlink()
        raise
