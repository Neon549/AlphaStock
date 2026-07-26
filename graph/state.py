# graph/state.py
# ============ 改动说明 ============
# 新增3个字段: backtest_request / backtest_report / backtest_summary
# 回测结果与分析报告走同一条 State 数据总线
# ==================================

from typing import TypedDict, Optional
from langgraph.graph.message import add_messages
from typing import Annotated


class TradingState(TypedDict):
    # 输入
    stock_code: str

    # 各 Agent 的分析结果
    fundamental_report: Optional[str]
    technical_report: Optional[str]
    sentiment_report: Optional[str]

    # 研究员辩论结果
    bull_argument: Optional[str]
    bear_argument: Optional[str]
    debate_rounds: int

    # 最终输出
    final_decision: Optional[str]
    risk_assessment: Optional[str]

    # ── 新增：回测相关 ──────────────────────────
    backtest_request: Optional[dict]    # 回测参数 {"strategy": "kdj_macd", "start_date": "20220101", ...}
    backtest_report: Optional[str]      # 回测文本报告
    backtest_summary: Optional[str]     # LLM对回测结果的中文解读

    # 意图识别传入的分析维度控制
    analyst_focus: Optional[str]      # technical / fundamental / sentiment / all
    # 用户上传文档内容
    user_doc_context: Optional[str]
    # 消息历史
    messages: Annotated[list, add_messages]