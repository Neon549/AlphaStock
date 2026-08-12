"""Per-run model selection without process-global mutation."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class ModelProfile:
    name: str
    quick: object
    deep: object
    planner: object | None = None


_active_profile: ContextVar[ModelProfile | None] = ContextVar("active_model_profile", default=None)


def build_model_profile(name: str = "smart") -> ModelProfile:
    """Create isolated LLM clients for one run; never assign module globals."""
    from config.llm_config import DASHSCOPE_API_KEY, FallbackLLM, _make_deepseek, _make_qwen, _qwen_backup

    mode = name if name in {"fast", "smart", "strong"} else "smart"
    if mode == "fast":
        quick = FallbackLLM(_make_deepseek("deepseek-chat", 0.1), _qwen_backup, "QuickLLM[fast]")
        deep = FallbackLLM(_make_deepseek("deepseek-chat", 0.1), _qwen_backup, "DeepLLM[fast]")
    elif mode == "strong":
        quick = FallbackLLM(_make_deepseek("deepseek-reasoner", 0.0), _make_deepseek("deepseek-chat", 0.0), "QuickLLM[strong]", fallback_mode="draft_only")
        deep = FallbackLLM(_make_deepseek("deepseek-reasoner", 0.0), _make_deepseek("deepseek-chat", 0.0), "DeepLLM[strong]", fallback_mode="draft_only")
    else:
        quick = FallbackLLM(_make_deepseek("deepseek-chat", 0.1), _qwen_backup, "QuickLLM[smart]")
        deep = FallbackLLM(_make_deepseek("deepseek-reasoner", 0.1), _make_deepseek("deepseek-chat", 0.1), "DeepLLM[smart]", fallback_mode="draft_only")
    # Planner is independent of the UI's fast/smart/strong profile: a wrong
    # Skill path costs more than a single stronger routing call. Qwen is
    # primary and DeepSeek Reasoner keeps an in-flight run available.
    if DASHSCOPE_API_KEY:
        planner = FallbackLLM(
            _make_qwen("qwen3.7-max", 0.0),
            _make_deepseek("deepseek-reasoner", 0.0),
            f"PlannerLLM[{mode}]",
            fallback_mode="full",
        )
    else:
        planner = FallbackLLM(
            _make_deepseek("deepseek-reasoner", 0.0),
            _make_deepseek("deepseek-chat", 0.0),
            f"PlannerLLM[{mode}]",
            fallback_mode="draft_only",
        )
    return ModelProfile(mode, quick, deep, planner)


@contextmanager
def model_scope(name: str = "smart") -> Iterator[ModelProfile]:
    from agent_runtime.reliability import model_retry_budget_scope

    with model_retry_budget_scope():
        token = _active_profile.set(build_model_profile(name))
        try:
            yield _active_profile.get()  # type: ignore[return-value]
        finally:
            _active_profile.reset(token)


def active_model(kind: str) -> object | None:
    profile = _active_profile.get()
    return getattr(profile, kind, None) if profile else None
