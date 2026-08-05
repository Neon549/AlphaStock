"""VLM extraction for user-uploaded investment images.

This module only extracts structured facts from an image.  Indexing the result
and document lifecycle management deliberately stay outside the model boundary.
"""

from __future__ import annotations

import base64
import os
from typing import Any


_DEFAULT_PROMPT = """请分析这张图片，提取所有财务和市场数据：

1. 如果是财报/财务数据截图：提取净利润、营收、ROE、PE、PB、毛利率、负债率等数字。
2. 如果是K线图：识别价格、趋势、支撑位、压力位、成交量和技术形态。
3. 如果是公告/新闻截图：提取关键事件、数字、日期和公司名称。

请用结构化格式输出；无法确认的字段要明确标注。"""


def analyze_image(image_bytes: bytes, image_type: str, question: str = "") -> dict[str, str]:
    """Use Qwen-VL to extract evidence; return a safe result on missing config."""

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        return {
            "extracted_data": "API Key未配置，无法分析图片",
            "analysis": "",
            "data_type": "unknown",
        }
    try:
        return _analyze_with_qwen_vl(
            base64.b64encode(image_bytes).decode("utf-8"),
            image_type,
            question or _DEFAULT_PROMPT,
            api_key,
        )
    except Exception as exc:
        return {
            "extracted_data": f"图片分析失败: {exc}",
            "analysis": "",
            "data_type": "unknown",
        }


def _analyze_with_qwen_vl(
    image_base64: str, image_type: str, prompt: str, api_key: str
) -> dict[str, str]:
    import requests

    response = requests.post(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "qwen-vl-plus",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{image_type};base64,{image_base64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": 1000,
        },
        timeout=30,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    if any(key in content for key in ("净利润", "ROE", "PE", "营收", "毛利率")):
        data_type = "financial"
    elif any(key in content for key in ("K线", "均线", "成交量", "支撑", "压力")):
        data_type = "kline"
    else:
        data_type = "other"
    return {"extracted_data": content, "analysis": "", "data_type": data_type}
