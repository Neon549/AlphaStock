from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3

from tools.backtest_tools import run_strategy_backtest, list_available_strategies
from langchain_core.messages import HumanMessage
from graph.state import TradingState
from agents.fundamental_analyst import run_fundamental_analysis
from agents.technical_analyst import run_technical_analysis
from agents.sentiment_analyst import run_sentiment_analysis
from config.llm_config import deep_llm
from memory.long_term import LongTermMemory
import time
import concurrent.futures
import re
from rag.strategy_indexer import retrieve_strategy_knowledge

memory = LongTermMemory()

ERROR_MARKERS = [
    "[TOOL_ERROR]",
    "[ANALYSIS_ABORT]",
    "数据不足，禁止基于假设继续分析",
    "数据不足，无法分析",
    "未找到股票代码",
    "获取股价失败",
    "获取财务指标失败",
    "获取历史数据失败",
    "检索失败",
    "NaN",
]

COMPANY_NAME_PATTERN = re.compile(r"股票名称[:：]\s*([^\n]+)")


def _contains_error(text: str | None) -> bool:
    if not text:
        return True

    text = text.strip()

    # 只有真正以失败结果开头，才算错误
    if text.startswith("[ANALYSIS_ABORT]"):
        return True

    if text.startswith("[TOOL_ERROR]"):
        return True

    return False


def _extract_company_names(*texts: str) -> set[str]:
    names = set()
    for text in texts:
        if not text:
            continue
        matches = COMPANY_NAME_PATTERN.findall(text)
        for m in matches:
            name = m.strip()
            if name and name != "名称未验证":
                names.add(name)
    return names


def analysts_node(state: TradingState) -> dict:
    stock_code = state["stock_code"]
    # analyst_focus 由意图识别层写入 state，决定启动哪些 Analyst
    # 取值：technical / fundamental / sentiment / all（默认）
    focus = state.get("analyst_focus") or "all"
    total_start = time.time()

    run_f = focus in ("all", "fundamental")
    run_t = focus in ("all", "technical")
    run_s = focus in ("all", "sentiment")

    active = [
        k for k, v in {"基本面": run_f, "技术面": run_t, "情绪": run_s}.items() if v
    ]
    print(f"\n🚀 分析师启动：{stock_code}，focus={focus}，运行：{active}")

    SKIP_MSG = "[SKIPPED] 本次分析未启用此维度"

    def run_fundamental():
        if not run_f:
            return SKIP_MSG
        start = time.time()
        print("📊 [基本面分析师] 开始...")
        result = run_fundamental_analysis(stock_code)
        print(f"✅ 基本面分析完成，用时 {time.time() - start:.2f}s")
        return result

    def run_technical():
        if not run_t:
            return SKIP_MSG
        start = time.time()
        print("📈 [技术面分析师] 开始...")
        result = run_technical_analysis(stock_code)
        print(f"✅ 技术面分析完成，用时 {time.time() - start:.2f}s")
        return result

    def run_sentiment():
        if not run_s:
            return SKIP_MSG
        start = time.time()
        print("📰 [情绪分析师] 开始...")
        result = run_sentiment_analysis(stock_code)
        print(f"✅ 情绪分析完成，用时 {time.time() - start:.2f}s")
        return result

    # 只有实际需要运行的任务才占线程，跳过的瞬间返回
    max_workers = sum([run_f, run_t, run_s]) or 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_fundamental = executor.submit(run_fundamental)
        future_technical = executor.submit(run_technical)
        future_sentiment = executor.submit(run_sentiment)

        fundamental_report = future_fundamental.result()
        technical_report = future_technical.result()
        sentiment_report = future_sentiment.result()

    print(f"✅ 分析师全部完成，总用时 {time.time() - total_start:.2f}s")

    return {
        "fundamental_report": fundamental_report,
        "technical_report": technical_report,
        "sentiment_report": sentiment_report,
    }


def validation_node(state: TradingState) -> dict:
    print("\n🛡️ [校验节点] 检查分析结果可靠性...")

    fundamental_report = state.get("fundamental_report", "")
    technical_report = state.get("technical_report", "")
    sentiment_report = state.get("sentiment_report", "")

    SKIP_MSG = "[SKIPPED] 本次分析未启用此维度"
    errors = []
    if fundamental_report != SKIP_MSG and _contains_error(fundamental_report):
        errors.append("基本面分析存在工具错误或数据不足")
    if technical_report != SKIP_MSG and _contains_error(technical_report):
        errors.append("技术面分析存在工具错误或数据不足")
    if sentiment_report != SKIP_MSG and _contains_error(sentiment_report):
        errors.append("情绪分析存在工具错误或数据不足")

    # 改为：三个都失败才中止
    if len(errors) >= 3:
        print("❌ 校验失败，三个分析师全部异常，系统将中止后续决策")
        return {
            "risk_assessment": "\n".join(errors),
            "final_decision": (
                "### 交易决策\n"
                "决策：数据不足，停止分析\n"
                "原因：三个分析师全部返回错误。\n"
            ),
        }

    if errors:
        print(f"⚠️ 部分校验警告（{len(errors)}个），但继续分析")

    print("✅ 校验通过，进入研究员节点")
    return {"risk_assessment": "校验通过"}


def should_continue_after_validation(state: TradingState) -> str:
    """
    条件路由：
    - 如果 final_decision 已经被 validation_node 写入，说明要中止
    - 否则继续 researcher
    """
    if state.get("final_decision"):
        return "abort"
    return "researcher"


def abort_node(state: TradingState) -> dict:
    """
    中止节点：直接结束，不再进入 researcher / trader
    """
    print("\n⛔ [中止节点] 已拦截不可靠分析，终止流程")
    return {
        "bull_argument": "已中止：上游分析结果不可靠",
        "bear_argument": state.get("risk_assessment", "检测到数据问题"),
    }


def researcher_node(state: TradingState) -> dict:
    """
    研究员节点：综合三份报告，进行多空辩论
    """
    print(f"\n🔬 [研究员] 综合分析，进行多空辩论...")

    prompt = f"""你是一位资深A股研究员，需要基于以下三份分析报告进行多空辩论。

重要要求：
1. 只能基于下述报告中的已验证内容分析
2. 禁止补充未出现的数据
3. 若报告中存在明显数据不足，应偏向保守结论

## 基本面分析报告
{state['fundamental_report']}

## 技术面分析报告
{state['technical_report']}

## 情绪分析报告
{state['sentiment_report']}

请分别给出：
### 多方观点（看涨理由）
[从三份报告中提炼支持买入的核心论据，3-5条]

### 空方观点（看跌理由）
[从三份报告中提炼支持卖出/观望的核心论据，3-5条]

### 综合倾向
[多方占优/空方占优/势均力敌] - [简要理由]
"""

    response = deep_llm.invoke([HumanMessage(content=prompt)])
    result = response.content
    print("✅ 多空辩论完成")

    return {
        "bull_argument": result,
        "bear_argument": result,
        "debate_rounds": state.get("debate_rounds", 0) + 1,
    }


def _calc_position_size(decision_text: str, confidence: str) -> str:
    """仓位量化：根据决策和置信度给出具体百分比"""
    if "强烈买入" in decision_text:
        return {"高": "30%", "中": "20%", "低": "10%"}.get(confidence, "15%")
    elif "买入" in decision_text:
        return {"高": "20%", "中": "10%", "低": "5%"}.get(confidence, "10%")
    return "0%"


def _fix_position_consistency(text: str, confidence: str) -> str:
    """后处理：修正仓位和决策一致性"""
    import re

    is_buy = any(x in text for x in ["强烈买入", "买入"])
    is_watch = any(x in text for x in ["持有观望", "观望", "不买", "停止分析"])
    if is_buy and not is_watch:
        pos = _calc_position_size(text, confidence)
        text = re.sub(r"建议仓位：.*", f"建议仓位：{pos}", text)
    elif is_watch:
        text = re.sub(r"建议仓位：.*", "建议仓位：0%", text)
    return text


def trader_node(state: TradingState) -> dict:
    """
    交易员节点：做出最终决策
    仓位量化：根据置信度自动计算具体百分比，确保和决策一致
    """
    print(f"\n💼 [交易员] 综合所有信息，做出最终决策...")

    history = memory.get_history(state["stock_code"])
    technical_context = (state.get("technical_report") or "")[:800]
    risk_assessment = state.get("risk_assessment") or ""

    # 从辩论结论提取置信度
    confidence = "中"
    if "置信度：高" in risk_assessment:
        confidence = "高"
    elif "置信度：低" in risk_assessment:
        confidence = "低"

    prompt = f"""你是一位经验丰富的A股交易员，需要基于研究团队的分析做出最终交易决策。

【强制规则 - 违反即为错误输出】
1. 只能依据已提供内容决策
2. 决策和建议仓位必须严格对应：
   - 买入/强烈买入 → 建议仓位必须是具体百分比（如10%/20%/30%）
   - 持有观望/不买 → 建议仓位必须是0%
   - 严禁"持有观望"配"空仓等待"这种矛盾写法
3. 所有价格必须是具体数字，严禁写"/"或"暂不设定"
4. 即使数据有缺失，也必须基于已有数据做出决策（买入/持有观望/不买），禁止输出"数据不足，停止分析"

## 历史决策记录
{history}

## 技术面数据（含当前价格）
{technical_context}

## 研究员多空辩论结论
{risk_assessment or state.get("bull_argument", "无")}

### 交易决策
决策：[强烈买入 / 买入 / 持有观望 / 不买 / 数据不足，停止分析]
建议仓位：[买入→20% 强烈买入→30% 观望/不买→0%]
操作价位：[具体价格区间，如75.00-78.00元]
目标价：[具体目标价，如95.00-100.00元]
止损价：[具体止损价，如70.00元]
持有周期：[短线1-2周 / 中线1-3月]

### 决策依据
[3条核心理由]

### 风险提示
[2-3条主要风险]
"""

    response = deep_llm.invoke([HumanMessage(content=prompt)])
    raw_decision = response.content
    final_decision = _fix_position_consistency(raw_decision, confidence)

    memory.save_decision(
        stock_code=state["stock_code"],
        decision=final_decision,
        fundamental_summary=(state.get("fundamental_report") or "")[:300],
        technical_summary=(state.get("technical_report") or "")[:300],
        sentiment_summary=(state.get("sentiment_report") or "")[:300],
    )

    print("✅ 交易决策完成并已存入记忆")
    return {"final_decision": final_decision}


def backtest_node(state: TradingState) -> dict:
    """
    回测节点：解析用户的回测请求，调用回测引擎
    触发条件：state中有 backtest_request 字段
    """
    print("\n📊 [回测节点] 开始执行量化回测...")

    req = state.get("backtest_request") or {}
    stock_code = req.get("stock_code", state.get("stock_code", ""))
    strategy = req.get("strategy", "kdj_macd")
    start_date = req.get("start_date", "20220101")
    end_date = req.get("end_date", "20261231")
    initial_cash = req.get("initial_cash", 100000.0)

    if not stock_code:
        return {
            "backtest_report": "[TOOL_ERROR]\nreason=股票代码为空",
            "backtest_summary": "回测失败：未提供股票代码。",
        }

    result = run_strategy_backtest.invoke(
        {
            "stock_code": stock_code,
            "strategy": strategy,
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": initial_cash,
        }
    )

    print(f"✅ 回测完成")
    return {"backtest_report": result}


def backtest_interpreter_node(state: TradingState) -> dict:
    print("\n🧠 [解读节点] LLM解读回测结果...")

    report = state.get("backtest_report", "")
    req = state.get("backtest_request") or {}

    if "[TOOL_ERROR]" in report:
        return {"backtest_summary": "回测数据异常，无法生成解读报告。"}

    # RAG检索相关策略知识
    rag_knowledge = retrieve_strategy_knowledge(
        f"{req.get('strategy', '')} 策略 夏普比率 回撤 胜率"
    )

    prompt = f"""你是一位专业的A股量化分析师，请基于以下回测结果和策略知识给出专业评价。

## 回测数据
{report}

## 相关策略知识库（请结合这些理论依据分析）
{rag_knowledge}

请从以下几个角度给出中文分析（每点2-3句话）：

### 1. 收益评价
[评价总收益率和夏普比率，与沪深300年化收益对比]

### 2. 风险评价  
[评价最大回撤，结合知识库中的可接受回撤标准判断]

### 3. 策略有效性
[根据交易次数和胜率判断，结合知识库中的统计显著性要求]

### 4. 优化建议
[结合知识库中的参数调整建议，给出1-2条具体优化方向]

### 5. 综合结论
[一句话总结：该策略是否值得进一步验证]

注意：回测结果基于历史数据，不构成投资建议。
"""

    response = deep_llm.invoke([HumanMessage(content=prompt)])
    summary = response.content
    print("✅ 回测解读完成")

    # 存入长期记忆
    memory.save_backtest_result(
        stock_code=req.get("stock_code", state.get("stock_code", "")),
        strategy=req.get("strategy", "unknown"),
        result_summary=report[:500],
    )
    print("💾 回测结果已存入长期记忆")

    return {"backtest_summary": summary}


def backtest_optimizer_node(state: TradingState) -> dict:
    """
    参数优化节点：对当前策略执行网格搜索，找到最优参数组合
    触发条件：回测完成后自动执行
    """
    print("\n⚙️ [优化节点] 开始参数网格搜索...")

    req = state.get("backtest_request") or {}
    stock_code = req.get("stock_code", state.get("stock_code", ""))
    strategy = req.get("strategy", "kdj_macd")
    start_date = req.get("start_date", "20220101")
    end_date = req.get("end_date", "20261231")

    try:
        import os
        from backtest.data_loader import get_stock_data_tushare, get_mock_data
        from backtest.optimizer import grid_search, format_optimization_result

        token = os.getenv("TUSHARE_TOKEN", "")
        if token:
            df = get_stock_data_tushare(stock_code, start_date, end_date, token)
        else:
            df = get_mock_data(stock_code, days=500)

        results = grid_search(df, strategy, top_n=3)
        opt_report = format_optimization_result(results, strategy)
        print("✅ 参数优化完成")

        # 存入记忆
        memory.save_backtest_result(
            stock_code=stock_code,
            strategy=f"{strategy}_optimized",
            result_summary=opt_report[:500],
        )

        return {
            "backtest_summary": state.get("backtest_summary", "")
            + "\n\n---\n"
            + opt_report
        }

    except Exception as e:
        print(f"[Optimizer] 优化失败: {e}")
        return {}


def build_trading_graph():
    """
    工作流：
    analysts -> validation
    validation -> abort 或 researcher
    researcher -> trader
    trader -> END
    abort -> END
    """
    graph = StateGraph(TradingState)

    graph.add_node("analysts", analysts_node)
    graph.add_node("validation", validation_node)
    graph.add_node("abort", abort_node)
    graph.add_node("researcher", researcher_node)
    graph.add_node("trader", trader_node)

    graph.set_entry_point("analysts")

    graph.add_edge("analysts", "validation")
    graph.add_conditional_edges(
        "validation",
        should_continue_after_validation,
        {
            "abort": "abort",
            "researcher": "researcher",
        },
    )
    graph.add_edge("researcher", "trader")
    graph.add_edge("trader", END)
    graph.add_edge("abort", END)
    # ── 回测子图（独立于主分析流程）──────────────
    graph.add_node("backtest", backtest_node)
    graph.add_node("backtest_interpreter", backtest_interpreter_node)
    graph.add_edge("backtest", "backtest_interpreter")
    graph.add_edge("backtest_interpreter", END)

    # ✅ Checkpoint：每个节点执行完自动保存State快照
    # Checkpoint持久化：每个节点执行完自动保存State到SQLite
    # 重启后可恢复，支持断点续跑
    conn = sqlite3.connect("checkpoints.db", check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return graph.compile(checkpointer=checkpointer)


def build_backtest_graph():
    graph = StateGraph(TradingState)
    graph.add_node("backtest", backtest_node)
    graph.add_node("backtest_interpreter", backtest_interpreter_node)
    graph.add_node("backtest_optimizer", backtest_optimizer_node)

    graph.set_entry_point("backtest")
    graph.add_edge("backtest", "backtest_interpreter")
    graph.add_edge("backtest_interpreter", "backtest_optimizer")
    graph.add_edge("backtest_optimizer", END)
    return graph.compile()


backtest_graph = build_backtest_graph()
trading_graph = build_trading_graph()


def run_trading_analysis(
    stock_code: str,
    doc_context: str = "",
    analyst_focus: str = "all",
) -> dict:
    """
    完整分析入口
    doc_context:    用户上传文档的相关内容，注入到分析上下文
    analyst_focus:  意图识别层传入，控制启动哪些 Analyst
                    取值：technical / fundamental / sentiment / all（默认）
    """
    initial_state = {
        "stock_code": stock_code,
        "analyst_focus": analyst_focus,  # ← 新增
        "fundamental_report": None,
        "technical_report": None,
        "sentiment_report": None,
        "bull_argument": None,
        "bear_argument": None,
        "debate_rounds": 0,
        "final_decision": None,
        "risk_assessment": None,
        "messages": [],
        "user_doc_context": doc_context if doc_context else "",
    }
    # Checkpoint thread_id：同一股票用同一ID，支持断点恢复
    config = {"configurable": {"thread_id": f"analysis_{stock_code}"}}
    return trading_graph.invoke(initial_state, config=config)


def run_backtest_analysis(
    stock_code: str,
    strategy: str = "kdj_macd",
    start_date: str = "20220101",
    end_date: str = "20261231",
    initial_cash: float = 100000.0,
) -> dict:
    """
    回测分析入口 —— 直接进入回测节点，不走完整分析流程
    供 api/routes.py 的 /backtest 接口调用
    """
    initial_state = {
        "stock_code": stock_code,
        "fundamental_report": None,
        "technical_report": None,
        "sentiment_report": None,
        "bull_argument": None,
        "bear_argument": None,
        "debate_rounds": 0,
        "final_decision": None,
        "risk_assessment": None,
        "backtest_request": {
            "stock_code": stock_code,
            "strategy": strategy,
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": initial_cash,
        },
        "backtest_report": None,
        "backtest_summary": None,
        "messages": [],
    }
    return backtest_graph.invoke(initial_state)
