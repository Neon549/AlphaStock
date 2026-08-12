"""PostgreSQL persistence for trace evaluation, badcases and review candidates."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from control_plane.contracts import AgentEvent, AgentRunResult
from agent_learning.trace_evaluation import build_learning_candidate, evaluate_agent_run


def _json(value: Any):
    from psycopg2.extras import Json

    return Json(value)


def _badcase_id(run_id: str, category: str) -> str:
    digest = sha256(f"{run_id}:{category}".encode("utf-8")).hexdigest()[:24]
    return f"badcase-{digest}"


def _severity(category: str, outcome: str) -> str:
    if outcome == "failed":
        return "high" if category in {"publication_policy", "evidence_contract"} else "medium"
    return "low"


def persist_learning_artifacts(cur: Any, event: AgentEvent, result: AgentRunResult) -> dict[str, Any]:
    """Persist deterministic evaluation in the same transaction as its run audit.

    Candidates remain ``pending_review`` and contain no automatic SFT/DPO
    labels.  This prevents an unreviewed financial draft from being silently
    promoted into post-training data.
    """

    evaluation = evaluate_agent_run(event, result)
    cur.execute(
        """
        INSERT INTO agent_run_evaluations
            (run_id, schema_version, outcome, score, rubric_results, badcase_types, summary)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id) DO UPDATE SET
            schema_version = EXCLUDED.schema_version,
            outcome = EXCLUDED.outcome,
            score = EXCLUDED.score,
            rubric_results = EXCLUDED.rubric_results,
            badcase_types = EXCLUDED.badcase_types,
            summary = EXCLUDED.summary,
            created_at = NOW()
        """,
        (
            result.run_id,
            evaluation["schema_version"],
            evaluation["outcome"],
            evaluation["score"],
            _json(evaluation["rubrics"]),
            _json(evaluation["badcase_types"]),
            _json(evaluation["summary"]),
        ),
    )

    if evaluation["outcome"] != "passed":
        categories = evaluation["badcase_types"] or ["evaluation_failure"]
        for category in categories:
            badcase_id = _badcase_id(result.run_id, str(category))
            cur.execute(
                """
                INSERT INTO agent_badcases
                    (badcase_id, run_id, category, severity, status, fingerprint, detail)
                VALUES (%s, %s, %s, %s, 'open', %s, %s)
                ON CONFLICT (run_id, category) DO UPDATE SET
                    severity = EXCLUDED.severity,
                    detail = EXCLUDED.detail
                """,
                (
                    badcase_id,
                    result.run_id,
                    str(category),
                    _severity(str(category), evaluation["outcome"]),
                    sha256(f"{result.route}:{category}".encode("utf-8")).hexdigest(),
                    _json(evaluation),
                ),
            )

    candidate = build_learning_candidate(event, result, evaluation)
    if candidate:
        cur.execute(
            """
            INSERT INTO agent_training_candidates
                (candidate_id, run_id, candidate_type, status, sample, evaluation)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id) DO NOTHING
            """,
            (
                candidate["candidate_id"],
                candidate["run_id"],
                candidate["candidate_type"],
                candidate["status"],
                _json(candidate["sample"]),
                _json(candidate["evaluation"]),
            ),
        )
    return evaluation


def review_training_candidate(
    candidate_id: str,
    *,
    approved: bool,
    reviewer: str,
    candidate_type: str | None = None,
    chosen: str | None = None,
    rejected: str | None = None,
    instruction: str | None = None,
    review_note: str = "",
) -> dict[str, Any] | None:
    """Human-review a captured trajectory and optionally label SFT/DPO data.

    A reviewer must supply the preferred answer; a generated investment draft
    is never silently promoted as the SFT target.  DPO also requires an
    explicit rejected answer, typically the original failed or weaker draft.
    """

    from db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, candidate_type, status, sample
                FROM agent_training_candidates
                WHERE candidate_id = %s
                FOR UPDATE
                """,
                (candidate_id,),
            )
            row = cur.fetchone()
            if not row or row[2] != "pending_review":
                return None
            run_id, existing_type, _, sample = row
            sample = dict(sample or {})
            if not approved:
                cur.execute(
                    """
                    UPDATE agent_training_candidates
                    SET status = 'rejected', reviewer = %s, review_note = %s, reviewed_at = NOW()
                    WHERE candidate_id = %s
                    """,
                    (reviewer, review_note[:2_000], candidate_id),
                )
                conn.commit()
                return {"candidate_id": candidate_id, "run_id": run_id, "status": "rejected"}

            final_type = str(candidate_type or existing_type)
            preferred = str(chosen or "").strip()
            if final_type not in {"sft", "dpo"}:
                raise ValueError("approved candidates must be labelled as sft or dpo")
            if not str(sample.get("prompt") or "").strip() or not preferred:
                raise ValueError("approved candidates require a prompt and reviewer-supplied chosen answer")
            sample["chosen"] = preferred
            if instruction is not None and str(instruction).strip():
                sample["instruction"] = str(instruction).strip()
            if final_type == "dpo":
                rejected_text = str(rejected or "").strip()
                if not rejected_text:
                    raise ValueError("approved DPO candidates require a rejected answer")
                sample["rejected"] = rejected_text
            else:
                sample.pop("rejected", None)
            cur.execute(
                """
                UPDATE agent_training_candidates
                SET candidate_type = %s, status = 'approved', sample = %s,
                    reviewer = %s, review_note = %s, reviewed_at = NOW()
                WHERE candidate_id = %s
                """,
                (final_type, _json(sample), reviewer, review_note[:2_000], candidate_id),
            )
        conn.commit()
    return {
        "candidate_id": candidate_id,
        "run_id": run_id,
        "status": "approved",
        "candidate_type": final_type,
    }
