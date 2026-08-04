"""Small, deterministic strategy guidance for backtest interpretation.

Backtest interpretation needs a handful of stable methodology notes, not a
vector database or a sentence-transformer process.  Keeping this local avoids
pulling legacy Chroma/OpenMP dependencies into the production backtest path.
"""

from __future__ import annotations


_KNOWLEDGE = (
    (
        "KDJ strategy discipline",
        ("kdj", "超卖", "k<25", "j<15"),
        "KDJ 在强趋势中会钝化；低位金叉应结合趋势和成交量过滤，不能单独视为买入保证。",
    ),
    (
        "MACD trend confirmation",
        ("macd", "dif", "dea", "趋势"),
        "MACD 金叉用于趋势确认，价格创新高而指标未创新高可能提示背离，需与入场信号区分。",
    ),
    (
        "RSI overbought and oversold",
        ("rsi", "超买", "超卖"),
        "RSI 阈值在 A 股需结合波动状态调整；单次超买或超卖不构成独立交易结论。",
    ),
    (
        "Risk-adjusted metrics",
        ("sharpe", "回撤", "drawdown", "收益", "胜率"),
        "评价回测需同时看年化收益、夏普、最大回撤和交易次数；高收益但高回撤不等于可实盘复用。",
    ),
    (
        "Backtest validity limits",
        ("过拟合", "样本", "滑点", "未来函数", "统计"),
        "警惕参数过拟合、幸存者偏差、未来函数和未计滑点；样本外验证与参数稳定性比单次最佳参数更重要。",
    ),
)


def retrieve_backtest_knowledge(query: str, k: int = 3) -> str:
    """Select stable methodology notes without external retrieval dependencies."""

    normalized = (query or "").lower()
    ranked = sorted(
        _KNOWLEDGE,
        key=lambda item: sum(keyword in normalized for keyword in item[1]),
        reverse=True,
    )
    return "\n\n".join(f"【{title}】\n{content}" for title, _keywords, content in ranked[:k])
