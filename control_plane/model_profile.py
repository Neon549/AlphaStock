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


_active_profile: ContextVar[ModelProfile | None] = ContextVar("active_model_profile", default=None)


def build_model_profile(name: str = "smart") -> ModelProfile:
    """Create isolated LLM clients for one run; never assign module globals."""
    from config.llm_config import FallbackLLM, _make_deepseek, _qwen_backup

    mode = name if name in {"fast", "smart", "strong"} else "smart"
    if mode == "fast":
        quick = FallbackLLM(_make_deepseek("deepseek-chat", 0.1), _qwen_backup, "QuickLLM[fast]")
        deep = FallbackLLM(_make_deepseek("deepseek-chat", 0.1), _qwen_backup, "DeepLLM[fast]")
    elif mode == "strong":
        quick = FallbackLLM(_make_deepseek("deepseek-reasoner", 0.0), _make_deepseek("deepseek-chat", 0.0), "QuickLLM[strong]")
        deep = FallbackLLM(_make_deepseek("deepseek-reasoner", 0.0), _make_deepseek("deepseek-chat", 0.0), "DeepLLM[strong]")
    else:
        quick = FallbackLLM(_make_deepseek("deepseek-chat", 0.1), _qwen_backup, "QuickLLM[smart]")
        deep = FallbackLLM(_make_deepseek("deepseek-reasoner", 0.1), _make_deepseek("deepseek-chat", 0.1), "DeepLLM[smart]")
    return ModelProfile(mode, quick, deep)


@contextmanager
def model_scope(name: str = "smart") -> Iterator[ModelProfile]:
    token = _active_profile.set(build_model_profile(name))
    try:
        yield _active_profile.get()  # type: ignore[return-value]
    finally:
        _active_profile.reset(token)


def active_model(kind: str) -> object | None:
    profile = _active_profile.get()
    return (profile.quick if kind == "quick" else profile.deep) if profile else None
