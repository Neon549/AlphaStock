"""
config/llm_config.py
AlphaStock 多模型路由配置

设计思路：
  不同Agent用不同模型，按任务复杂度分配：
  - quick_llm：DeepSeek-V3，快速便宜，用于情绪面/格式化任务
  - deep_llm：DeepSeek-R1，推理强，用于基本面/Validator复杂决策
  - backup_llm：Qwen-Plus，主力挂了自动切备用

.env 配置：
  DEEPSEEK_API_KEY=your_deepseek_key
  DASHSCOPE_API_KEY=your_qwen_key
  TUSHARE_TOKEN=your_tushare_token
  LANGFUSE_PUBLIC_KEY=your_langfuse_public_key
  LANGFUSE_SECRET_KEY=your_langfuse_secret_key
  LANGFUSE_HOST=http://localhost:3000
"""

import os
import sys
import time
import uuid
import requests as _requests
from pathlib import Path
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from agent_runtime.reliability import (
    ModelInvocationError,
    invoke_model_with_failure_policy,
)

# This module is also imported directly by offline tests and CLI tools.  Keep
# its startup diagnostics safe on Windows terminals configured for GBK.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── 加载环境变量 ─────────────────────────────────────────────────────
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
MODEL_REQUEST_TIMEOUT_SECONDS = float(os.getenv("AGENT_MODEL_REQUEST_TIMEOUT_SECONDS", "45"))

if not DEEPSEEK_API_KEY:
    raise ValueError("DEEPSEEK_API_KEY 未设置，请在 .env 文件中配置")


# ── LangFuse 初始化 ───────────────────────────────────────────────────

LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", os.getenv("LANGFUSE_BASE_URL", "http://localhost:3000"))

_langfuse = None
_langfuse_run_roots: dict[str, tuple[object, object]] = {}


def _get_langfuse():
    """懒加载 LangFuse 客户端，未配置时返回 None 不报错"""
    global _langfuse
    if _langfuse is not None:
        return _langfuse
    if not LANGFUSE_PUBLIC_KEY or not LANGFUSE_SECRET_KEY:
        return None
    try:
        from langfuse import Langfuse

        try:
            _langfuse = Langfuse(
                public_key=LANGFUSE_PUBLIC_KEY,
                secret_key=LANGFUSE_SECRET_KEY,
                host=LANGFUSE_HOST,
            )
        except TypeError:
            # Current SDKs prefer base_url; keep existing v2 self-hosted envs working.
            _langfuse = Langfuse(
                public_key=LANGFUSE_PUBLIC_KEY,
                secret_key=LANGFUSE_SECRET_KEY,
                base_url=LANGFUSE_HOST,
            )
        print(f"✅ LangFuse 已连接：{LANGFUSE_HOST}")
        return _langfuse
    except Exception as e:
        print(f"⚠️  LangFuse 初始化失败（不影响运行）: {e}")
        return None


def _langfuse_trace(run_id: str, *, name: str, metadata: dict | None = None, input_value=None):
    """Return a v2 trace bound to a runtime run id, without blocking the run."""
    lf = _get_langfuse()
    if lf is None:
        return None
    kwargs = {"id": run_id, "name": name}
    if metadata:
        kwargs["metadata"] = metadata
    if input_value is not None:
        kwargs["input"] = input_value
    try:
        return lf.trace(**kwargs)
    except Exception as exc:
        print(f"[LangFuse] trace creation failed (non-blocking): {exc}")
        return None


def _modern_root(run_id: str):
    """Return a v3/v4 root observation, if this request uses the OTEL SDK."""
    item = _langfuse_run_roots.get(run_id)
    return item[1] if item else None


def start_langfuse_run_trace(run_id: str, *, query: dict, metadata: dict | None = None) -> None:
    """Start one root trace per request; the query is redacted by observability."""
    lf = _get_langfuse()
    if lf is None:
        return
    root_metadata = {"run_id": run_id, "kind": "research_run", **(metadata or {})}
    # langfuse-python >=3 is OpenTelemetry based.  Holding this context open
    # makes every generation and RAG child observation belong to one request.
    if hasattr(lf, "start_as_current_observation"):
        try:
            context = lf.start_as_current_observation(
                as_type="span",
                name="alphastock/run",
                input={"query": query},
                metadata=root_metadata,
            )
            root = context.__enter__()
            _langfuse_run_roots[run_id] = (context, root)
            return
        except Exception as exc:
            print(f"[LangFuse] modern root trace failed (non-blocking): {exc}")
    _langfuse_trace(
        run_id,
        name="alphastock/run",
        input_value={"query": query},
        metadata=root_metadata,
    )


def finish_langfuse_run_trace(run_id: str, *, summary: dict) -> None:
    """Attach a safe operational summary to the root trace."""
    modern = _langfuse_run_roots.pop(run_id, None)
    if modern:
        context, root = modern
        try:
            root.update(output={"trace_summary": summary})
            context.__exit__(None, None, None)
            _get_langfuse().flush()
        except Exception as exc:
            print(f"[LangFuse] modern trace completion failed (non-blocking): {exc}")
        return
    trace = _langfuse_trace(run_id, name="alphastock/run")
    if trace is None:
        return
    try:
        trace.update(output={"trace_summary": summary})
        _get_langfuse().flush()
    except Exception as exc:
        print(f"[LangFuse] trace completion failed (non-blocking): {exc}")


def trace_langfuse_rag_event(run_id: str, *, event: str, payload: dict) -> None:
    """Attach retrieval/citation metadata as a child span of the root trace."""
    root = _modern_root(run_id)
    if root is not None:
        try:
            if hasattr(root, "start_observation"):
                span = root.start_observation(name=f"rag/{event}", as_type="span", input=payload)
            else:
                span = root.start_span(name=f"rag/{event}", input=payload)
            span.end()
            _get_langfuse().flush()
        except Exception as exc:
            print(f"[LangFuse] modern RAG span failed (non-blocking): {exc}")
        return
    trace = _langfuse_trace(run_id, name="alphastock/run")
    if trace is None:
        return
    try:
        span = trace.span(name=f"rag/{event}", input=payload)
        if hasattr(span, "end"):
            span.end()
        _get_langfuse().flush()
    except Exception as exc:
        print(f"[LangFuse] RAG span failed (non-blocking): {exc}")


def _trace(
    name: str,
    input_text: str,
    output_text: str,
    model: str,
    latency_ms: float,
    success: bool,
    used_backup: bool = False,
    usage: dict | None = None,
    recovery: dict | None = None,
):
    """上报一次 LLM 调用到 LangFuse"""
    lf = _get_langfuse()
    if lf is None:
        return
    try:
        metadata = {
            "model": model,
            "success": success,
            "used_backup": used_backup,
            "latency_ms": round(latency_ms, 1),
        }
        if usage:
            metadata["usage"] = usage
        if recovery:
            metadata["recovery"] = recovery

        try:
            from control_plane.observability import current_run_id
            run_id = current_run_id()
        except Exception:
            run_id = None
        if run_id:
            metadata["run_id"] = run_id

        safe_input = _safe_trace_input(input_text)
        root = _modern_root(run_id) if run_id else None
        if root is not None:
            try:
                if hasattr(root, "start_observation"):
                    generation = root.start_observation(
                        name=name, as_type="generation", model=model,
                        input=safe_input, metadata=metadata,
                    )
                else:
                    generation = root.start_generation(
                        name=name, model=model, input=safe_input, metadata=metadata,
                    )
                generation.update(output=output_text[:2000])
                generation.end()
                lf.flush()
                return
            except Exception as exc:
                print(f"[LangFuse] modern generation failed (non-blocking): {exc}")

        trace = _langfuse_trace(
            run_id or f"llm-{uuid.uuid4()}",
            name="alphastock/run" if run_id else f"alphastock/{name}",
            metadata=metadata,
        )
        if trace is None:
            return
        trace.generation(
            name=name,
            model=model,
            input=safe_input,
            output=output_text[:2000],
            metadata={
                "latency_ms": round(latency_ms, 1),
                **({"usage": usage} if usage else {}),
                **({"recovery": recovery} if recovery else {}),
            },
        )
        lf.flush()
    except Exception as e:
        print(f"[LangFuse] 上报失败（不影响运行）: {e}")


# ── 模型工厂 ──────────────────────────────────────────────────────────


def _make_deepseek(model: str, temperature: float = 0.1) -> ChatOpenAI:
    """创建 DeepSeek 模型实例"""
    return ChatOpenAI(
        model=model,
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com",
        temperature=temperature,
        timeout=MODEL_REQUEST_TIMEOUT_SECONDS,
        # FallbackLLM owns the cross-provider retry budget. Disable SDK-level
        # retries so hidden retries cannot exceed that budget or obscure trace.
        max_retries=0,
    )


def _make_qwen(model: str, temperature: float = 0.1) -> ChatOpenAI:
    """创建通义千问备用模型实例"""
    if not DASHSCOPE_API_KEY:
        raise ValueError("DASHSCOPE_API_KEY 未设置，无法使用备用模型")
    return ChatOpenAI(
        model=model,
        api_key=DASHSCOPE_API_KEY,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        temperature=temperature,
        timeout=MODEL_REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
    )


# ── 带自动降级 + LangFuse 追踪的 LLM 包装 ────────────────────────────


def _msg_to_str(messages) -> str:
    """把 messages 转成可读字符串用于追踪"""
    try:
        if isinstance(messages, list):
            return " | ".join(getattr(m, "content", str(m))[:300] for m in messages)
        return str(messages)[:300]
    except Exception:
        return ""


def _safe_trace_input(text: str) -> dict:
    """Keep prompt telemetry useful without exporting raw user or tool context."""
    try:
        from control_plane.observability import redact_query
        return redact_query(text)
    except Exception:
        return {"input_length": len(text)}


def _extract_usage(result) -> dict:
    """兼容不同 OpenAI/LangChain 版本，提取 token 与 DeepSeek 缓存用量。"""
    usage_metadata = getattr(result, "usage_metadata", None) or {}
    response_metadata = getattr(result, "response_metadata", None) or {}
    token_usage = response_metadata.get("token_usage", {}) or {}

    def pick(*keys):
        for source in (usage_metadata, token_usage, response_metadata):
            for key in keys:
                value = source.get(key) if isinstance(source, dict) else None
                if value is not None:
                    return value
        return None

    input_details = usage_metadata.get("input_token_details", {}) if isinstance(usage_metadata, dict) else {}
    cache_read = input_details.get("cache_read") if isinstance(input_details, dict) else None
    cache_hit = pick("prompt_cache_hit_tokens")
    if cache_hit is None:
        cache_hit = cache_read

    fields = {
        "input_tokens": pick("input_tokens", "prompt_tokens"),
        "output_tokens": pick("output_tokens", "completion_tokens"),
        "total_tokens": pick("total_tokens"),
        "prompt_cache_hit_tokens": cache_hit,
        "prompt_cache_miss_tokens": pick("prompt_cache_miss_tokens"),
    }
    return {key: value for key, value in fields.items() if value is not None}


def _record_run_telemetry(
    model: str,
    latency_ms: float,
    success: bool,
    used_backup: bool,
    usage: dict | None,
    *,
    recovery: dict | None = None,
) -> None:
    """Record one provider attempt against the ContextVar-bound run, if any."""
    try:
        from control_plane.observability import record_llm_call
        record_llm_call(
            model=model,
            latency_ms=latency_ms,
            success=success,
            used_backup=used_backup,
            usage=usage,
            recovery=recovery,
        )
    except Exception:
        # Telemetry must never alter model availability.
        return


class FallbackLLM:
    """Typed retry, circuit breaking and compatible model failover.

    The wrapper only recovers transient *provider* failures.  Invalid request,
    context and authentication failures keep their structured error so the
    caller can compact, repair or block safely instead of hiding a bad request
    behind a different model.  ``draft_only`` fallback is intentionally
    observable and must still pass the downstream evidence/HITL gates.
    """

    def __init__(self, primary, backup=None, name: str = "LLM", *, fallback_mode: str = "full"):
        if fallback_mode not in {"full", "draft_only"}:
            raise ValueError("fallback_mode must be 'full' or 'draft_only'")
        self.primary = primary
        self.backup = backup
        self.name = name
        self.fallback_mode = fallback_mode

    @staticmethod
    def _provider_name(provider, fallback: str) -> str:
        return str(getattr(provider, "model_name", None) or getattr(provider, "model", None) or fallback)

    def _observe_attempt(self, messages, event: dict) -> None:
        """Write every physical provider attempt without persisting prompt text."""
        success = bool(event.get("success"))
        failure = event.get("failure") or {}
        result = event.get("result")
        usage = _extract_usage(result) if success else None
        used_backup = event.get("provider_role") == "backup"
        recovery = {
            "provider_role": event.get("provider_role"),
            "attempt": event.get("attempt"),
            "recovery_action": event.get("recovery_action"),
            "circuit_state": event.get("circuit_state"),
            "failure_type": failure.get("error_type"),
            "retry_delay_seconds": event.get("retry_delay_seconds"),
            "degradation_mode": self.fallback_mode if used_backup else "none",
        }
        output_text = (
            getattr(result, "content", str(result)) if success
            else str(failure.get("message") or "model provider failed")
        )
        model = str(event.get("provider_name") or self.name)
        _trace(
            name=self.name,
            input_text=_msg_to_str(messages),
            output_text=output_text,
            model=model,
            latency_ms=float(event.get("latency_ms") or 0.0),
            success=success,
            used_backup=used_backup,
            usage=usage,
            recovery=recovery,
        )
        _record_run_telemetry(
            model,
            float(event.get("latency_ms") or 0.0),
            success,
            used_backup,
            usage,
            recovery=recovery,
        )
        if success and "prompt_cache_hit_tokens" in (usage or {}):
            print(
                f"[{self.name}] Prompt Cache：hit={usage['prompt_cache_hit_tokens']} "
                f"miss={usage.get('prompt_cache_miss_tokens', 'unknown')}"
            )
        elif not success:
            print(
                f"[{self.name}] {event.get('provider_role')} model failure "
                f"({failure.get('error_type', 'UNKNOWN')}); {event.get('recovery_action')}"
            )

    def invoke(self, messages, **kwargs):
        return invoke_model_with_failure_policy(
            self.name,
            lambda: self.primary.invoke(messages, **kwargs),
            primary_name=self._provider_name(self.primary, f"{self.name}:primary"),
            backup_invoke=(lambda: self.backup.invoke(messages, **kwargs)) if self.backup else None,
            backup_name=self._provider_name(self.backup, f"{self.name}:backup") if self.backup else None,
            fallback_mode=self.fallback_mode,
            on_attempt=lambda event: self._observe_attempt(messages, event),
        ).result

    async def ainvoke(self, messages, **kwargs):
        # Keep asynchronous callers on the same bounded provider policy.  The
        # synchronous SDK method runs off the event loop, preventing an async
        # caller from bypassing retry/circuit/audit behavior.
        import asyncio

        return await asyncio.to_thread(self.invoke, messages, **kwargs)

    def stream(self, messages, **kwargs):
        try:
            yield from self.primary.stream(messages, **kwargs)
        except Exception as e:
            if self.backup:
                print(f"[{self.name}] 主力模型失败，切换备用: {e}")
                yield from self.backup.stream(messages, **kwargs)
            else:
                raise

    # 让它可以被 bind_tools 等方法正常使用
    def __getattr__(self, name):
        return getattr(self.primary, name)


# ── 模型实例（按Agent分配）────────────────────────────────────────────

# 备用模型（Qwen）
_qwen_backup = None
if DASHSCOPE_API_KEY:
    try:
        _qwen_backup = _make_qwen("qwen3.7-max")
    except Exception:
        pass

# quick_llm：快速便宜，用于：
#   - SentimentAnalyst（情绪面分析）
#   - Validator（格式化验证）
#   - 所有需要快速响应的节点
_default_quick_llm = FallbackLLM(
    primary=_make_deepseek("deepseek-chat", temperature=0.1),
    backup=_qwen_backup,
    name="QuickLLM",
)

# deep_llm：推理强，用于：
#   - FundamentalAnalyst（基本面需要理解财务逻辑）
#   - Validator多空裁判（需要综合复杂信息）
#   - BacktestInterpreter（量化策略解读）
_default_deep_llm = FallbackLLM(
    primary=_make_deepseek("deepseek-reasoner", temperature=0.1),
    backup=_make_deepseek("deepseek-chat", temperature=0.1),  # R1挂了降级V3
    name="DeepLLM",
    # Chat is a compatible transport fallback, but it is weaker on multi-step
    # reasoning. In AlphaStock it may produce only a governed draft, never an
    # automatic high-risk decision.
    fallback_mode="draft_only",
)


_default_planner_llm = FallbackLLM(
    primary=_qwen_backup or _make_deepseek("deepseek-reasoner", temperature=0.0),
    backup=(
        _make_deepseek("deepseek-reasoner", temperature=0.0)
        if _qwen_backup
        else _make_deepseek("deepseek-chat", temperature=0.0)
    ),
    name="PlannerLLM",
    fallback_mode="full" if _qwen_backup else "draft_only",
)


class _ProfileRoutedLLM:
    """Compatibility proxy: select a ContextVar-bound client per run."""

    def __init__(self, kind, fallback):
        self.kind = kind
        self.fallback = fallback

    def _target(self):
        from control_plane.model_profile import active_model
        return active_model(self.kind) or self.fallback

    def invoke(self, messages, **kwargs):
        return self._target().invoke(messages, **kwargs)

    async def ainvoke(self, messages, **kwargs):
        return await self._target().ainvoke(messages, **kwargs)


quick_llm = _ProfileRoutedLLM("quick", _default_quick_llm)
deep_llm = _ProfileRoutedLLM("deep", _default_deep_llm)
planner_llm = _ProfileRoutedLLM("planner", _default_planner_llm)

# ── 模型路由表（给Agent查询用）────────────────────────────────────────

MODEL_ROUTING = {
    "technical_analyst": "TechLens本地模型（DeepSeek降级）",
    "fundamental_analyst": "deepseek-reasoner（推理强）",
    "sentiment_analyst": "deepseek-chat（快速便宜）",
    "validator": "deepseek-reasoner（综合裁判）",
    "backtest_interpreter": "deepseek-reasoner（策略解读）",
    "trader": "deepseek-chat（快速决策）",
}


def print_model_routing():
    """打印当前模型路由配置"""
    print("\n📊 AlphaStock 模型路由配置：")
    for agent, model in MODEL_ROUTING.items():
        print(f"   {agent:<25} → {model}")
    print()


# ── TechLens 本地推理客户端 ───────────────────────────────────────────


class TechLensClient:
    """
    TechLens-1.5B 本地推理客户端
    优先使用本地模型，不可用时自动降级到 DeepSeek
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        health_timeout_seconds: float | None = None,
        request_timeout_seconds: float | None = None,
    ):
        # The service may run beside the API in development or on a separate
        # GPU host in production; neither address belongs in source code.
        self.base_url = (base_url or os.getenv("TECHLENS_BASE_URL", "http://127.0.0.1:8088")).rstrip("/")
        self.health_timeout_seconds = health_timeout_seconds or float(
            os.getenv("TECHLENS_HEALTH_TIMEOUT_SECONDS", "3")
        )
        self.request_timeout_seconds = request_timeout_seconds or float(
            os.getenv("TECHLENS_REQUEST_TIMEOUT_SECONDS", "60")
        )

    def analyze(
        self,
        stock_code: str,
        history_result: str,
        price_result: str,
        kdj_result: str,
    ) -> dict:
        resp = _requests.post(
            f"{self.base_url}/analyze",
            json={
                "stock_code": stock_code,
                "history_result": history_result,
                "price_result": price_result,
                "kdj_result": kdj_result,
            },
            timeout=self.request_timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def is_available(self) -> bool:
        try:
            r = _requests.get(f"{self.base_url}/health", timeout=self.health_timeout_seconds)
            return r.status_code == 200
        except Exception:
            return False


techlens_client = TechLensClient()

# ── 启动日志 ──────────────────────────────────────────────────────────

print("✅ AlphaStock LLM配置加载完成")
print(f"   主力：DeepSeek API {'✅' if DEEPSEEK_API_KEY else '❌'}")
print(f"   备用：Qwen API {'✅' if DASHSCOPE_API_KEY else '❌（未配置，不影响运行）'}")
print(
    f"   LangFuse：{'✅ ' + LANGFUSE_HOST if LANGFUSE_PUBLIC_KEY else '⚠️  未配置（不影响运行）'}"
)
print(
    f"   TechLens本地模型：{'✅ 在线' if techlens_client.is_available() else '⚠️ 离线（自动降级DeepSeek）'}"
)
