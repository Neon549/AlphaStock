#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# agents/validator.py
# 验证Agent：检查各分析师结论是否一致，进行自我纠正

from config.llm_config import quick_llm
from config.prompt_cache import build_cacheable_messages
from agent_runtime.agents.skill_loader import load_skill

# ========== 兜底 VALIDATOR_PROMPT ==========
VALIDATOR_PROMPT = """你是一个量化交易决策验证专家。

你的任务是：
1. 检查各分析师的结论是否一致
2. 发现矛盾时指出问题
3. 给出最终是否买入的建议

判断标准：
- 基本面差（ROE<10%）→ 直接否决
- 技术面KDJ信号不满足 → 直接否决  
- 情绪面极度负面 → 降低评分
- 多空辩论空方明显占优 → 否决

输出格式：
## 验证结论

### 一致性检查
[各Agent结论是否一致，有无矛盾]

### 最终建议
买入/观望/不买

### 理由
[50字以内]

### 置信度
高/中/低
"""


def run_validator(
    stock_code: str,
    fundamental_report: str,
    technical_report: str,
    sentiment_report: str,
    researcher_analysis: str,
) -> dict:
    """
    验证各Agent结论一致性，输出最终建议
    返回: {decision, confidence, reason, consistent}
    """
    # ✅ 从 SKILL.md 加载验证规范（找不到则用兜底）
    system_prompt = load_skill("stock_analysis") or VALIDATOR_PROMPT

    prompt = f"""{system_prompt}

## 股票代码
{stock_code}

## 基本面分析结论
{fundamental_report[-500:] if len(fundamental_report) > 500 else fundamental_report}

## 技术面分析结论  
{technical_report[-500:] if len(technical_report) > 500 else technical_report}

## 情绪面分析结论
{sentiment_report[-500:] if len(sentiment_report) > 500 else sentiment_report}

## 研究员辩论结论
{researcher_analysis[-500:] if len(researcher_analysis) > 500 else researcher_analysis}

请验证以上结论并给出最终建议。
"""

    response = quick_llm.invoke(build_cacheable_messages(system_prompt, prompt[len(system_prompt):]))
    text = response.content

    # 解析结论
    decision = "观望"
    confidence = "低"

    if "买入" in text and "不买" not in text:
        decision = "买入"
    elif "不买" in text:
        decision = "不买"

    if "高" in text:
        confidence = "高"
    elif "中" in text:
        confidence = "中"

    return {
        "decision": decision,
        "confidence": confidence,
        "report": text,
        "consistent": "矛盾" not in text,
    }
