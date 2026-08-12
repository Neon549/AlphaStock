"""Deterministic context compaction for evidence-sensitive investment runs.

This is intentionally not a free-form LLM summary of financial facts.  Raw
reports remain in their source stores and traces; prompt context receives a
bounded view containing provenance, freshness and a short source preview.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from agent_runtime.context.budget import estimate_tokens
from config.runtime_paths import CACHE_DIR


CONTEXT_OVERFLOW = re.compile(r"(?:\b413\b|context.{0,40}(?:length|long|limit)|prompt.{0,40}(?:length|long|limit))", re.I | re.S)


def persist_tool_result(
    *,
    tool: str,
    content: str,
    source_kind: str,
    citations: list[dict[str, Any]],
) -> str:
    """Persist the full local tool payload outside prompt context.

    This is a transient run artifact, not long-term Memory.  Document source
    truth remains in its document store; this cache makes a large result
    inspectable while its next-turn prompt representation stays small.
    """

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    directory = CACHE_DIR / "tool_results"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{tool}-{digest}.json"
    if not path.exists():
        path.write_text(
            json.dumps(
                {
                    "tool": tool,
                    "source_kind": source_kind,
                    "citations": citations,
                    "content": content,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    result_ref = f"runtime:tool-result:{tool}:{digest}"
    # A run buffers artifacts in memory and flushes them to PostgreSQL only
    # after the parent agent_run row exists.  This keeps result_ref durable
    # without letting a tool failure become a database availability failure.
    try:
        from control_plane.observability import register_tool_artifact
        register_tool_artifact(
            result_ref,
            {
                "tool": tool,
                "source_kind": source_kind,
                "citations": citations,
                "content": content,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
        )
    except Exception:
        pass
    return result_ref


def is_context_overflow_error(exc: BaseException) -> bool:
    """Recognise provider-independent prompt/context overflow failures."""

    return bool(CONTEXT_OVERFLOW.search(str(exc)))


def compact_tool_observations(
    observations: list[dict[str, Any]],
    *,
    max_tokens: int = 1_200,
    preview_chars: int = 480,
) -> tuple[list[dict[str, Any]], bool]:
    """Return a traceable, bounded tool view for the next LLM turn.

    The newest observation is favoured.  Older content is replaced by its
    provenance and evidence IDs before any source wording is truncated.
    """

    compacted: list[dict[str, Any]] = []
    changed = False
    used = 0
    for index, observation in enumerate(reversed(observations)):
        content = str(observation.get("content") or "")
        item = {
            "tool": observation.get("tool"),
            "ok": bool(observation.get("ok")),
            "source_kind": observation.get("source_kind", "evidence"),
            "citations": observation.get("citations", []),
            "tool_metadata": observation.get("tool_metadata", {}),
            "freshness": observation.get("freshness", {}),
            "tool_failure": observation.get("tool_failure"),
            "degraded": bool(observation.get("degraded")),
            "content_ref": observation.get("result_ref") or (
                f"tool:{observation.get('tool', 'unknown')}:"
                f"{hashlib.sha256(content.encode('utf-8')).hexdigest()[:12]}"
            ),
        }
        # Keep a slightly larger preview for the newest result.  Once the
        # shared budget is close to full, retain only provenance for old calls.
        permitted_preview = preview_chars if index == 0 else preview_chars // 2
        preview = content[:permitted_preview]
        if len(content) > len(preview):
            preview += "\n[full result omitted from prompt; use evidence ID/source to re-fetch]"
            changed = True
        item["preview"] = preview
        size = estimate_tokens(json.dumps(item, ensure_ascii=False))
        if used + size > max_tokens:
            item.pop("preview", None)
            item["content_omitted"] = True
            size = estimate_tokens(json.dumps(item, ensure_ascii=False))
            changed = True
        if used + size > max_tokens:
            # Evidence ids are more useful than an unauditable character cut.
            item = {
                "tool": item["tool"],
                "ok": item["ok"],
                "citations": item["citations"],
                "content_ref": item["content_ref"],
                "content_omitted": True,
            }
            size = estimate_tokens(json.dumps(item, ensure_ascii=False))
            changed = True
        compacted.append(item)
        used += size

    compacted.reverse()
    return compacted, changed


def emergency_tool_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Last safe retry view after a provider rejects prompt size (e.g. HTTP 413)."""

    compacted, _ = compact_tool_observations(observations[-1:], max_tokens=260, preview_chars=120)
    return compacted
