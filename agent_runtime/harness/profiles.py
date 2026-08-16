"""Business profiles for one unified Harness runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    permission: str
    description: str
    network: bool = False
    side_effect: str = "read"


@dataclass(frozen=True)
class Profile:
    name: str
    max_steps: int
    tools: tuple[ToolSpec, ...]
    sandbox: str = "safe"

    def tool(self, name: str) -> ToolSpec | None:
        return next((item for item in self.tools if item.name == name), None)

    def available(self, granted: set[str], *, has_session_document: bool) -> list[dict[str, str]]:
        return [
            {"name": item.name, "description": item.description}
            for item in self.tools
            if item.permission in granted and (item.name != "document-rag" or has_session_document)
        ]


class ProfileRegistry:
    def __init__(self, profiles: Iterable[Profile] = ()) -> None:
        self._profiles = {profile.name: profile for profile in profiles}

    def get(self, name: str) -> Profile:
        try:
            return self._profiles[name]
        except KeyError as exc:
            raise KeyError(f"unknown harness profile: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))


RESEARCH = Profile(
    name="research",
    max_steps=3,
    tools=(
        ToolSpec("document-rag", "document:read", "Search session-scoped uploaded-document evidence. Arguments: {query}"),
        ToolSpec("market-price", "market:read", "Get current price and basic market verification. No arguments needed.", network=True),
        ToolSpec("market-history", "market:read", "Get bounded daily K-line history for deterministic price evidence. No arguments needed.", network=True),
        ToolSpec("financial-indicators", "market:read", "Get financial indicators for the requested stock. No arguments needed.", network=True),
        ToolSpec("stock-news", "market:read", "Get recent stock news. No arguments needed.", network=True),
        ToolSpec("memory-search", "memory:read", "Search approved Agent-memory Markdown for reusable operating knowledge. Arguments: {query}"),
    ),
)

INVESTMENT = Profile(
    name="investment",
    max_steps=4,
    tools=(
        ToolSpec("analysis", "market:read", "Run one or more specialist analyses in parallel. Arguments: {focuses:[technical,fundamental,sentiment]}"),
        ToolSpec("document-rag", "document:read", "Search the current session's uploaded documents. Arguments: {query}"),
        ToolSpec("backtest", "backtest:run", "Run one bounded historical strategy backtest. Arguments: {strategy,start_date,end_date}"),
        ToolSpec("market-price", "market:read", "Retrieve a timestamped current market-price evidence record. No arguments needed.", network=True),
        ToolSpec("market-history", "market:read", "Retrieve bounded daily K-line history as structured market evidence. No arguments needed.", network=True),
        ToolSpec("financial-indicators", "market:read", "Retrieve timestamped financial indicators with reporting-period freshness. No arguments needed.", network=True),
        ToolSpec("memory-search", "memory:read", "Search approved operational memory. It is guidance, never current market evidence. Arguments: {query}"),
    ),
)


DEFAULT_PROFILES = ProfileRegistry((RESEARCH, INVESTMENT))
