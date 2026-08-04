# graph/scan_graph.py
from typing import TypedDict, List
from langgraph.graph import StateGraph, END
from backtest.signal_scanner import scan_today
from agent_runtime.agents.fundamental_analyst import run_fundamental_analysis
from agent_runtime.agents.technical_analyst import run_technical_analysis
from agent_runtime.agents.sentiment_analyst import run_sentiment_analysis
from agent_runtime.agents.validator import run_validator


class ScanState(TypedDict):
    candidates: List[dict]
    current_index: int
    analysis_results: List[dict]
    final_recommendations: List[dict]


def build_scan_graph(base_start: str = None, strategy: str = "all"):

    def scan_node(state: ScanState) -> ScanState:
        print("🔍 开始扫描今日买点...")
        candidates = scan_today(top_n=5, base_start=base_start, strategy=strategy)
        print(f"✅ 找到{len(candidates)}只候选股")
        return {**state, "candidates": candidates, "current_index": 0}

    def analyze_node(state: ScanState) -> ScanState:
        candidates = state["candidates"]
        results = state.get("analysis_results", [])

        for candidate in candidates:
            code = candidate["code"]
            name = candidate["name"]
            print(f"\n📊 分析 {name}({code})...")
            try:
                fundamental = run_fundamental_analysis(code)
                technical = run_technical_analysis(code)
                sentiment = run_sentiment_analysis(code)
                validation = run_validator(
                    stock_code=code,
                    fundamental_report=fundamental,
                    technical_report=technical,
                    sentiment_report=sentiment,
                    researcher_analysis="",
                )
                results.append(
                    {
                        "code": code,
                        "name": name,
                        "k": candidate["k"],
                        "j": candidate["j"],
                        "close": candidate["close"],
                        "decision": validation["decision"],
                        "confidence": validation["confidence"],
                        "consistent": validation["consistent"],
                        "report": validation["report"],
                    }
                )
                print(
                    f"→ 决策: {validation['decision']} 置信度: {validation['confidence']}"
                )
            except Exception as e:
                print(f"❌ {name}分析失败: {e}")

        return {**state, "analysis_results": results}

    def recommend_node(state: ScanState) -> ScanState:
        results = state["analysis_results"]
        recommendations = [
            r
            for r in results
            if r["decision"] == "买入" and r["confidence"] in ["高", "中"]
        ]
        recommendations.sort(key=lambda x: x["j"])
        print(f"\n✅ 最终推荐{len(recommendations)}只股票")
        return {**state, "final_recommendations": recommendations}

    graph = StateGraph(ScanState)
    graph.add_node("scan", scan_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("recommend", recommend_node)
    graph.set_entry_point("scan")
    graph.add_edge("scan", "analyze")
    graph.add_edge("analyze", "recommend")
    graph.add_edge("recommend", END)
    return graph.compile()


def run_daily_scan(base_start: str = None, strategy: str = "all") -> dict:
    scan_graph = build_scan_graph(base_start=base_start, strategy=strategy)
    initial_state = {
        "candidates": [],
        "current_index": 0,
        "analysis_results": [],
        "final_recommendations": [],
    }
    return scan_graph.invoke(initial_state)
