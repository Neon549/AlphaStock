"""Evidence references used by checkpoints and resumed runs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent_runtime.context.compaction import persist_tool_result
from config.runtime_paths import CACHE_DIR


_REF = re.compile(r"^runtime:tool-result:([A-Za-z0-9_.-]+):([a-f0-9]{16})$")


class EvidenceManager:
    def put(
        self,
        *,
        tool: str,
        content: str,
        source_kind: str,
        citations: list[dict[str, Any]],
        stock_code: str | None = None,
    ) -> str:
        return persist_tool_result(
            tool=tool,
            content=content,
            source_kind=source_kind,
            citations=citations,
            stock_code=stock_code,
        )

    def get(self, result_ref: str) -> dict[str, Any] | None:
        match = _REF.match(str(result_ref))
        if not match:
            return None
        tool, digest = match.groups()
        path = Path(CACHE_DIR) / "tool_results" / f"{tool}-{digest}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None


# Short internal imports stay readable, while the public architecture names
# the component by its responsibility.
Evidence = EvidenceManager
