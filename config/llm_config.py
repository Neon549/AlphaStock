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
import time
import uuid
import requests as _requests
from pathlib import Path
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# ── 加载环境变量 ─────────────────────────────────────────────────────
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")

if not DEEPSEEK_API_KEY:
    raise ValueError("DEEPSEEK_API_KEY 未设置，请在 .env 文件中配置")


# ── LangFuse 初始化 ───────────────────────────────────────────────────

LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3000")

_langfuse = None


def _get_langfuse():
    """懒加载 LangFuse 客户端，未配置时返回 None 不报错"""
    global _langfuse
    if _langfuse is not None:
        return _langfuse
    if not LANGFUSE_PUBLIC_KEY or not LANGFUSE_SECRET_KEY:
        return None
    try:
        from langfuse import Langfuse

        _langfuse = Langfuse(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_HOST,
        )
        print(f"✅ LangFuse 已连接：{LANGFUSE_HOST}")
        return _langfuse
    except Exception as e:
        print(f"⚠️  LangFuse 初始化失败（不影响运行）: {e}")
        return None


def _trace(
    name: str,
    input_text: str,
    output_text: str,
    model: str,
    latency_ms: float,
    success: bool,
    used_backup: bool = False,
):
    """上报一次 LLM 调用到 LangFuse"""
    lf = _get_langfuse()
    if lf is None:
        return
    try:
        trace = lf.trace(
            name=f"alphastock/{name}",
            metadata={
                "model": model,
                "success": success,
                "used_backup": used_backup,
                "latency_ms": round(latency_ms, 1),
            },
        )
        trace.generation(
            name=name,
            model=model,
            input=input_text[:2000],  # 防止太长
            output=output_text[:2000],
            metadata={"latency_ms": round(latency_ms, 1)},
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


class FallbackLLM:
    """
    带自动降级 + LangFuse 追踪的 LLM 包装器
    主力模型失败时自动切换备用模型，每次调用自动上报到 LangFuse

    用法和普通LLM完全一样：
      response = fallback_llm.invoke([HumanMessage(content="...")])
    """

    def __init__(self, primary, backup=None, name: str = "LLM"):
        self.primary = primary
        self.backup = backup
        self.name = name

    def invoke(self, messages, **kwargs):
        t0 = time.time()
        used_backup = False
        try:
            result = self.primary.invoke(messages, **kwargs)
            latency = (time.time() - t0) * 1000
            _trace(
                name=self.name,
                input_text=_msg_to_str(messages),
                output_text=getattr(result, "content", str(result)),
                model=getattr(self.primary, "model_name", self.name),
                latency_ms=latency,
                success=True,
            )
            return result
        except Exception as e:
            if self.backup:
                print(f"[{self.name}] 主力模型失败，切换备用: {e}")
                used_backup = True
                result = self.backup.invoke(messages, **kwargs)
                latency = (time.time() - t0) * 1000
                _trace(
                    name=self.name,
                    input_text=_msg_to_str(messages),
                    output_text=getattr(result, "content", str(result)),
                    model=getattr(self.backup, "model_name", "backup"),
                    latency_ms=latency,
                    success=True,
                    used_backup=True,
                )
                return result
            latency = (time.time() - t0) * 1000
            _trace(
                name=self.name,
                input_text=_msg_to_str(messages),
                output_text=str(e),
                model=getattr(self.primary, "model_name", self.name),
                latency_ms=latency,
                success=False,
            )
            raise

    async def ainvoke(self, messages, **kwargs):
        t0 = time.time()
        try:
            result = await self.primary.ainvoke(messages, **kwargs)
            latency = (time.time() - t0) * 1000
            _trace(
                name=self.name,
                input_text=_msg_to_str(messages),
                output_text=getattr(result, "content", str(result)),
                model=getattr(self.primary, "model_name", self.name),
                latency_ms=latency,
                success=True,
            )
            return result
        except Exception as e:
            if self.backup:
                print(f"[{self.name}] 主力模型失败，切换备用: {e}")
                result = await self.backup.ainvoke(messages, **kwargs)
                latency = (time.time() - t0) * 1000
                _trace(
                    name=self.name,
                    input_text=_msg_to_str(messages),
                    output_text=getattr(result, "content", str(result)),
                    model=getattr(self.backup, "model_name", "backup"),
                    latency_ms=latency,
                    success=True,
                    used_backup=True,
                )
                return result
            raise

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
quick_llm = FallbackLLM(
    primary=_make_deepseek("deepseek-chat", temperature=0.1),
    backup=_qwen_backup,
    name="QuickLLM",
)

# deep_llm：推理强，用于：
#   - FundamentalAnalyst（基本面需要理解财务逻辑）
#   - Validator多空裁判（需要综合复杂信息）
#   - BacktestInterpreter（量化策略解读）
deep_llm = FallbackLLM(
    primary=_make_deepseek("deepseek-reasoner", temperature=0.1),
    backup=_make_deepseek("deepseek-chat", temperature=0.1),  # R1挂了降级V3
    name="DeepLLM",
)

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

    def __init__(self, base_url: str = "http://localhost:8088"):
        self.base_url = base_url

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
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def is_available(self) -> bool:
        try:
            r = _requests.get(f"{self.base_url}/health", timeout=3)
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
