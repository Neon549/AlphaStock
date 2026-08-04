"""Small deterministic prompt-budget helper used before downstream LLM calls."""

from __future__ import annotations

from dataclasses import dataclass


class ContextBudgetExceeded(ValueError):
    """A required request cannot fit safely without losing material content."""


@dataclass(frozen=True)
class ContextBlock:
    name: str
    content: str
    priority: int


def estimate_tokens(text: str) -> int:
    """Conservative local estimate; provider usage remains the source of truth."""
    chinese = sum("\u4e00" <= char <= "\u9fff" for char in text)
    other = len(text) - chinese
    return int(chinese * 1.2 + other / 4) + 1


def pack_context(blocks: list[ContextBlock], max_tokens: int = 6_000) -> dict:
    """Keep high-priority blocks; 70% soft threshold, 85% hard threshold."""
    soft_limit = int(max_tokens * 0.70)
    hard_limit = int(max_tokens * 0.85)
    selected: list[ContextBlock] = []
    omitted: list[str] = []
    used = 0
    for block in sorted(blocks, key=lambda item: item.priority, reverse=True):
        size = estimate_tokens(block.content)
        if used + size > hard_limit:
            omitted.append(block.name)
        elif used + size > soft_limit and block.priority < 80:
            # At 70%, drop optional history/tool-output blocks first.  Critical
            # structured evidence is never character-sliced here.
            omitted.append(block.name)
        else:
            selected.append(block)
            used += size
    return {
        "text": "\n\n".join(f"## {block.name}\n{block.content}" for block in selected),
        "estimated_tokens": used,
        "soft_limit": soft_limit,
        "hard_limit": hard_limit,
        "mode": "normal" if used <= soft_limit and not omitted else "compacted",
        "omitted_blocks": omitted,
    }
