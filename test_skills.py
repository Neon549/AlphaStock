#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
@author: yulin
@created: 2026/7/26 18:03
@updated: 2026/7/26 18:03
@version: 1.0
@description:
"""

# 在项目根目录下运行：python test_skills.py

from pathlib import Path
from agents.skill_loader import load_analyst_skill, load_backtest_skill


def test_all_skills():
    results = []

    # ── 1. 测试每个analyst都能加载 ──────────────────
    for focus in ["fundamental", "technical", "sentiment"]:
        prompt = load_analyst_skill(focus)

        checks = {
            "非空": len(prompt) > 0,
            "包含common_rules": "通用硬性规则" in prompt,
            "包含ANALYSIS_OK": "ANALYSIS_OK" in prompt,
            "包含ANALYSIS_ABORT": "ANALYSIS_ABORT" in prompt,
            "无跨域内容": _check_no_cross_domain(focus, prompt),
        }

        print(f"\n{'='*40}")
        print(f"[{focus}_analyst]")
        for name, ok in checks.items():
            status = "✅" if ok else "❌"
            print(f"  {status} {name}")
            results.append((f"{focus}/{name}", ok))

    # ── 2. 测试backtest skill ────────────────────────
    for node in ["strategy", "interpreter", "all"]:
        prompt = load_backtest_skill(node)

        checks = {
            "非空": len(prompt) > 0,
            "包含BACKTEST标记": "BACKTEST" in prompt,
        }

        print(f"\n{'='*40}")
        print(f"[backtest/{node}]")
        for name, ok in checks.items():
            status = "✅" if ok else "❌"
            print(f"  {status} {name}")
            results.append((f"backtest_{node}/{name}", ok))

    # ── 3. 测试未知focus的降级处理 ───────────────────
    print(f"\n{'='*40}")
    print("[未知focus降级测试]")
    prompt = load_analyst_skill("unknown_focus")
    ok = "通用硬性规则" in prompt  # common_rules还在
    print(f"  {'✅' if ok else '❌'} 未知focus时common_rules仍加载")
    results.append(("unknown_focus/common_rules仍加载", ok))

    # ── 4. 汇总 ──────────────────────────────────────
    print(f"\n{'='*40}")
    failed = [name for name, ok in results if not ok]
    print(f"总计：{len(results)} 项检查")
    print(f"通过：{len(results) - len(failed)}")
    print(f"失败：{len(failed)}")
    if failed:
        print("\n失败项：")
        for name in failed:
            print(f"  ❌ {name}")
    else:
        print("\n🎉 全部通过")


# 把 _check_no_cross_domain 函数改成这样
def _check_no_cross_domain(focus: str, prompt: str) -> bool:
    """只检查专属rules部分，common_rules里提到其他域关键词是正常的"""
    # 按分隔符拆开，取最后一段（专属rules）
    parts = prompt.split("---")
    specific_part = parts[-1] if len(parts) >= 2 else prompt

    forbidden = {
        "fundamental": ["KDJ策略信号", "趋势分析", "量价关系", "新闻情绪", "情绪评分"],
        "technical": ["PE/PB", "ROE分析", "营收增长", "新闻情绪", "情绪评分"],
        "sentiment": ["PE/PB", "ROE分析", "KDJ策略信号", "趋势分析", "量价关系"],
    }
    for keyword in forbidden.get(focus, []):
        if keyword in specific_part:
            print(f"    ⚠️  跨域关键词: {keyword}")
            return False
    return True


if __name__ == "__main__":
    test_all_skills()
