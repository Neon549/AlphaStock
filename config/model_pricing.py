"""Versioned model pricing used by the canonical runtime metrics contract.

Prices are USD per one million tokens.  The built-in table is intentionally
small: an unknown model makes cost estimation incomplete instead of silently
assuming that it is free.  Deployments can add or replace entries with
``ALPHASTOCK_MODEL_PRICING_JSON`` without changing runtime code.
"""

from __future__ import annotations

import json
import os
from typing import Any


BUILTIN_PRICING_VERSION = "2026-08-28"
BUILTIN_PRICES_USD_PER_MILLION: dict[str, dict[str, float]] = {
    # https://api-docs.deepseek.com/quick_start/pricing-details-usd
    "deepseek-chat": {"input": 0.27, "cache_hit": 0.07, "output": 1.10},
    "deepseek-reasoner": {"input": 0.55, "cache_hit": 0.14, "output": 2.19},
}


def load_model_pricing() -> tuple[str, dict[str, dict[str, float]]]:
    """Return the effective immutable pricing snapshot for this process."""

    prices = {model: dict(values) for model, values in BUILTIN_PRICES_USD_PER_MILLION.items()}
    raw = os.getenv("ALPHASTOCK_MODEL_PRICING_JSON", "").strip()
    if raw:
        overrides: Any = json.loads(raw)
        if not isinstance(overrides, dict):
            raise ValueError("ALPHASTOCK_MODEL_PRICING_JSON must be a JSON object")
        for model, values in overrides.items():
            if not isinstance(values, dict):
                raise ValueError(f"pricing for {model} must be an object")
            required = {"input", "cache_hit", "output"}
            if not required.issubset(values):
                raise ValueError(f"pricing for {model} requires input, cache_hit and output")
            parsed = {key: float(values[key]) for key in required}
            if any(value < 0 for value in parsed.values()):
                raise ValueError(f"pricing for {model} must be non-negative")
            prices[str(model)] = parsed
    version = os.getenv("ALPHASTOCK_MODEL_PRICING_VERSION", BUILTIN_PRICING_VERSION).strip()
    return version or BUILTIN_PRICING_VERSION, prices


def estimate_llm_cost(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Estimate billed cost and expose whether every successful call was priced."""

    try:
        version, prices = load_model_pricing()
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "cost_usd": 0.0,
            "cost_estimation_complete": False,
            "pricing_version": os.getenv("ALPHASTOCK_MODEL_PRICING_VERSION", "invalid") or "invalid",
            "pricing_currency": "USD",
            "unknown_pricing_models": [],
            "missing_usage_call_count": 0,
            "pricing_error_type": type(exc).__name__,
        }
    total_usd = 0.0
    unknown_models: set[str] = set()
    missing_usage_calls = 0
    for call in calls:
        model = str(call.get("model") or "unknown")
        input_tokens = int(call.get("input_tokens") or 0)
        output_tokens = int(call.get("output_tokens") or 0)
        cache_hit_tokens = int(call.get("prompt_cache_hit_tokens") or 0)
        cache_miss_raw = call.get("prompt_cache_miss_tokens")
        cache_miss_tokens = (
            int(cache_miss_raw)
            if cache_miss_raw is not None
            else max(0, input_tokens - cache_hit_tokens)
        )
        has_usage = any(call.get(field) is not None for field in (
            "input_tokens", "output_tokens", "total_tokens",
            "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
        ))
        if bool(call.get("success")) and not has_usage:
            missing_usage_calls += 1
        if not has_usage:
            continue
        price = prices.get(model)
        if price is None:
            unknown_models.add(model)
            continue
        total_usd += (
            cache_miss_tokens * price["input"]
            + cache_hit_tokens * price["cache_hit"]
            + output_tokens * price["output"]
        ) / 1_000_000
    return {
        "cost_usd": round(total_usd, 10),
        "cost_estimation_complete": not unknown_models and missing_usage_calls == 0,
        "pricing_version": version,
        "pricing_currency": "USD",
        "unknown_pricing_models": sorted(unknown_models),
        "missing_usage_call_count": missing_usage_calls,
        "pricing_error_type": None,
    }
