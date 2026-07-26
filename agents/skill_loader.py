"""
skill_loader.py
根据 intent 和 analyst_focus 动态加载对应的 skill 文件注入 System Prompt
"""

import os
from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent / "skills"  # skills/ 目录


def _load_file(path: Path) -> str:
    """读取单个 md 文件，文件不存在时返回空字符串并打印警告"""
    if not path.exists():
        print(f"[SKILL_LOADER WARNING] 文件不存在: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def load_analyst_skill(analyst_focus: str) -> str:
    """
    加载 stock_analysis skill。
    先加载 common_rules，再加载 analyst_focus 对应的专属规则。

    Args:
        analyst_focus: "fundamental" | "technical" | "sentiment"

    Returns:
        拼接后的完整 System Prompt 注入内容
    """
    refs_dir = SKILLS_DIR / "stock_analysis" / "refs"

    focus_map = {
        "fundamental": "fundamental_rules.md",
        "technical": "technical_rules.md",
        "sentiment": "sentiment_rules.md",
    }

    if analyst_focus not in focus_map:
        print(
            f"[SKILL_LOADER WARNING] 未知 analyst_focus: {analyst_focus}，跳过专属规则加载"
        )
        specific = ""
    else:
        specific = _load_file(refs_dir / focus_map[analyst_focus])

    common = _load_file(refs_dir / "common_rules.md")

    return f"{common}\n\n---\n\n{specific}".strip()


def load_backtest_skill(node: str = "all") -> str:
    """
    加载 backtest skill。

    Args:
        node: "strategy" | "interpreter" | "all"

    Returns:
        拼接后的完整 System Prompt 注入内容
    """
    refs_dir = SKILLS_DIR / "backtest" / "refs"

    if node == "strategy":
        return _load_file(refs_dir / "strategy_rules.md")
    elif node == "interpreter":
        return _load_file(refs_dir / "interpreter_rules.md")
    else:
        strategy = _load_file(refs_dir / "strategy_rules.md")
        interpreter = _load_file(refs_dir / "interpreter_rules.md")
        return f"{strategy}\n\n---\n\n{interpreter}".strip()


# ── 使用示例 ──────────────────────────────────────────────

if __name__ == "__main__":
    # 加载技术面 analyst 的完整 System Prompt
    technical_prompt = load_analyst_skill("technical")
    print("=== technical analyst skill ===")
    print(technical_prompt[:300], "...\n")

    # 加载基本面 analyst
    fundamental_prompt = load_analyst_skill("fundamental")
    print("=== fundamental analyst skill ===")
    print(fundamental_prompt[:300], "...\n")

    # 加载回测解读节点
    interpreter_prompt = load_backtest_skill("interpreter")
    print("=== backtest interpreter skill ===")
    print(interpreter_prompt[:300], "...")
