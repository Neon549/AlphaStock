"""Convert only human-approved learning candidates into post-training JSONL."""

from __future__ import annotations

from typing import Any


DEFAULT_AGENT_INSTRUCTION = (
    "You are a governed research agent. Follow the task constraints, distinguish "
    "evidence from assumptions, and return only the human-reviewed answer."
)


def candidate_to_training_row(
    candidate: dict[str, Any], *, output_format: str = "generic-jsonl"
) -> dict[str, Any]:
    """Render one approved SFT or DPO candidate for a supported training format.

    ``trajectory`` candidates are intentionally review artifacts only.  They
    cannot be exported until a reviewer upgrades them to SFT or DPO and adds
    an explicit preferred answer (and a rejected answer for DPO).
    """

    candidate_type = str(candidate.get("candidate_type") or "")
    status = str(candidate.get("status") or "")
    sample = candidate.get("sample") or {}
    if status != "approved":
        raise ValueError("only approved candidates can be exported")
    if not isinstance(sample, dict):
        raise ValueError("candidate sample must be an object")

    prompt = str(sample.get("prompt") or "").strip()
    chosen = str(sample.get("chosen") or "").strip()
    if not prompt or not chosen:
        raise ValueError("approved training candidates require prompt and chosen")

    metadata = {
        "candidate_id": candidate.get("candidate_id"),
        "source_run_id": candidate.get("run_id"),
        "reviewer": candidate.get("reviewer"),
        "schema_version": "agent-training-export/v1",
    }
    instruction = str(sample.get("instruction") or DEFAULT_AGENT_INSTRUCTION).strip()
    if output_format not in {"generic-jsonl", "llamafactory-alpaca"}:
        raise ValueError(f"unsupported output format: {output_format}")
    if candidate_type == "sft":
        if output_format == "llamafactory-alpaca":
            return {"instruction": instruction, "input": prompt, "output": chosen}
        return {
            "messages": [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": chosen},
            ],
            "metadata": metadata,
        }
    if candidate_type == "dpo":
        rejected = str(sample.get("rejected") or "").strip()
        if not rejected:
            raise ValueError("approved DPO candidates require rejected")
        if output_format == "llamafactory-alpaca":
            return {
                "instruction": instruction,
                "input": prompt,
                "chosen": chosen,
                "rejected": rejected,
            }
        return {"prompt": prompt, "chosen": chosen, "rejected": rejected, "metadata": metadata}
    raise ValueError("trajectory candidates must be reviewed and relabelled as sft or dpo first")
