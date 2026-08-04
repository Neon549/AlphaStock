"""Profiled context-window assembly for runtime and agent-loop calls.

The context window is not another memory database.  It is a short-lived,
token-budgeted view built from bootstrap rules, selected skill summaries,
session memory, user preferences and the current request.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runtime.context.budget import ContextBlock, ContextBudgetExceeded, estimate_tokens, pack_context


BOOTSTRAP_DIR = Path(__file__).parent / "bootstrap"
_BOOTSTRAP_FILES = ("AGENT.md", "IDENTITY.md", "TOOLS.md")
_MAX_BOOTSTRAP_CHARS_PER_FILE = 4_000
_MAX_CONTEXT_TOKENS = 6_000


@dataclass(frozen=True)
class ContextWindow:
    profile: str
    text: str
    estimated_tokens: int
    mode: str
    soft_limit: int
    hard_limit: int
    omitted_blocks: list[str]


class ContextWindowBuilder:
    """Build only the context appropriate for an LLM role.

    ``research`` is used by the bounded research harness. ``discussion`` is
    used for non-operational chat. Data-collection analysts intentionally do
    not receive prior transcript or preferences: their outputs must stay tied
    to the current market/document evidence.
    """

    def __init__(self, bootstrap_dir: Path = BOOTSTRAP_DIR):
        self.bootstrap_dir = bootstrap_dir

    def _bootstrap(self) -> str:
        parts: list[str] = []
        for filename in _BOOTSTRAP_FILES:
            path = self.bootstrap_dir / filename
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8").strip()[:_MAX_BOOTSTRAP_CHARS_PER_FILE]
            if content:
                parts.append(f"### {filename}\n{content}")
        return "\n\n".join(parts)

    def build(
        self,
        *,
        profile: str,
        user_message: str,
        memory_context: dict[str, Any],
        selected_skill_summaries: list[str] | None = None,
    ) -> ContextWindow:
        if profile not in {"research", "discussion"}:
            raise ValueError(f"unsupported context profile: {profile}")
        if estimate_tokens(user_message) > int(_MAX_CONTEXT_TOKENS * 0.85):
            raise ContextBudgetExceeded(
                "current user request exceeds the safe prompt budget; split the request or attach it as a document"
            )

        session = memory_context.get("session") or {}
        preferences = memory_context.get("preferences") or {}
        transcript = memory_context.get("recent_transcript") or []
        transcript_text = "\n".join(
            f"{turn.get('role', 'unknown')}: {str(turn.get('content', ''))}"
            for turn in transcript
        )
        blocks = [
            ContextBlock("bootstrap rules", self._bootstrap(), 100),
            ContextBlock("selected skill summaries", "\n".join(selected_skill_summaries or []), 85),
            ContextBlock("user preferences (non-evidence)", str(preferences), 70),
            ContextBlock("session summary (non-evidence)", str(session), 70),
            ContextBlock("recent session transcript (non-evidence)", transcript_text, 60),
            ContextBlock("current user request", user_message.strip(), 100),
        ]
        packed = pack_context(
            [block for block in blocks if block.content.strip()], max_tokens=_MAX_CONTEXT_TOKENS
        )
        return ContextWindow(
            profile=profile,
            text=packed["text"],
            estimated_tokens=packed["estimated_tokens"],
            mode=packed["mode"],
            soft_limit=packed["soft_limit"],
            hard_limit=packed["hard_limit"],
            omitted_blocks=packed["omitted_blocks"],
        )
