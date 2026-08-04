#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
@author: yulin
@created: 2026/6/1 17:30
@updated: 2026/6/1 17:30
@version: 1.0
@description:
"""

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 自动化策略优化Agent
# 循环运行回测 → LLM分析问题 → 调整参数 → 再回测 → 直到满足停止条件

import os
import json
import re
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

from langchain_core.messages import HumanMessage
from config.llm_config import quick_llm
from backtest.engine import run_backtest
from backtest.data_loader import get_stock_data_tushare

OPTIMIZER_PROMPT = """你是一位专业的量化策略优化专家。

当前是第{round_num}轮优化，目标夏普比率 >= {target_sharpe}。

## 本轮回测结果
收益率: {total_return:+.1f}%
夏普比率: {sharpe:.3f}
最大回撤: {max_drawdown:.1f}%
交易次数: {trade_count}
胜率: {win_rate}%

## 当前参数
{params}

## 历史优化记录
{history}

## 问题诊断要求
请分析当前策略的主要问题，并给出下一轮参数调整建议。

参数范围限制：
- k_threshold: 15~40
- j_threshold: 0~25
- stop_loss: 0.05~0.10   # 左侧入场止损要小
- take_profit: 0.08~0.20  # 止盈空间大一点
- crash_filter: 0.10~0.25

严格输出JSON格式，不要有任何其他文字：
{{
    "问题分析": "简述当前策略问题",
    "调整方向": "简述调整思路",
    "new_params": {{
        "k_threshold": 25,
        "j_threshold": 15,
        "stop_loss": 0.08,
        "take_profit": 0.08,
        "crash_filter": 0.15
    }}
}}
"""


def auto_optimize(
    stock_code: str,
    start_date: str,
    end_date: str,
    max_rounds: int = 10,
    target_sharpe: float = 0.8,
    min_trades: int = 3,
    patience: int = 3,
) -> tuple[dict, dict]:
    """
    自动化策略优化

    参数:
        stock_code:     股票代码
        start_date:     回测开始日期
        end_date:       回测结束日期
        max_rounds:     最大优化轮数
        target_sharpe:  目标夏普比率
        min_trades:     最少交易次数（低于此值说明参数太严）
        patience:       连续N轮无提升则停止

    返回:
        (best_params, best_result)
    """
    token = os.getenv("TUSHARE_TOKEN", "")
    print(f"[AutoOptimizer] 开始优化 {stock_code} {start_date}~{end_date}")
    print(f"[AutoOptimizer] 目标夏普={target_sharpe} 最大轮数={max_rounds}")

    df = get_stock_data_tushare(stock_code, start_date, end_date, token)
    print(f"[AutoOptimizer] 数据加载完成: {len(df)}根K线")

    # 初始参数
    params = {
        "k_threshold": 20,
        "j_threshold": 10,
        "stop_loss": 0.08,
        "take_profit": 0.12,
        "crash_filter": 0.15,
    }

    best_result = None
    best_params = params.copy()
    history = []
    no_improve_count = 0

    for round_num in range(1, max_rounds + 1):
        print(f"\n{'='*50}")
        print(f"第{round_num}轮 | 参数: {params}")

        # 跑回测
        try:
            result = run_backtest(
                df=df,
                strategy_name="kdj_oversold",
                initial_cash=100000,
                strategy_params=params.copy(),
            )
        except Exception as e:
            print(f"回测失败: {e}")
            break

        sharpe = result.get("sharpe") or -999
        total_return = result.get("total_return", 0)
        max_drawdown = result.get("max_drawdown", 0)
        win_rate = result.get("win_rate", 0)
        trade_count = result.get("trade_count", 0)

        print(
            f"收益={total_return:+.1f}% 夏普={sharpe:.3f} 回撤={max_drawdown:.1f}% 交易={trade_count}次 胜率={win_rate}%"
        )

        history.append(
            {
                "round": round_num,
                "params": params.copy(),
                "sharpe": round(sharpe, 3),
                "total_return": round(total_return, 1),
                "trade_count": trade_count,
            }
        )

        # 更新最优
        if best_result is None or sharpe > (best_result.get("sharpe") or -999):
            best_result = result
            best_params = params.copy()
            no_improve_count = 0
            print(f"✅ 新的最优结果！夏普={sharpe:.3f}")
        else:
            no_improve_count += 1
            print(f"⏳ 无提升 ({no_improve_count}/{patience})")

        # 停止条件1：达到目标夏普
        if sharpe >= target_sharpe:
            print(f"\n🎯 达到目标夏普{target_sharpe}，优化完成！")
            break

        # 停止条件2：连续N轮无提升
        if no_improve_count >= patience:
            print(f"\n⏹ 连续{patience}轮无提升，停止优化")
            break

        # 停止条件3：最后一轮不需要LLM
        if round_num == max_rounds:
            print(f"\n⏹ 达到最大轮数{max_rounds}，停止优化")
            break

        # 停止条件4：交易次数太少
        if trade_count < min_trades:
            print(
                f"\n⚠️ 交易次数{trade_count}低于最低{min_trades}次，参数过严，放宽后重试"
            )

        # LLM分析并给出新参数
        print(f"🤖 LLM分析中...")
        prompt = OPTIMIZER_PROMPT.format(
            round_num=round_num,
            target_sharpe=target_sharpe,
            total_return=total_return,
            sharpe=sharpe,
            max_drawdown=max_drawdown,
            trade_count=trade_count,
            win_rate=win_rate,
            params=json.dumps(params, ensure_ascii=False),
            history=json.dumps(history[-5:], ensure_ascii=False),
        )

        try:
            response = quick_llm.invoke([HumanMessage(content=prompt)])
            text = response.content
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                suggestion = json.loads(json_match.group())
                new_params = suggestion.get("new_params", {})
                print(f"💡 问题: {suggestion.get('问题分析', '')}")
                print(f"💡 方向: {suggestion.get('调整方向', '')}")
                print(f"💡 新参数: {new_params}")
                params.update(new_params)
            else:
                print("LLM未返回有效JSON，跳过本轮调整")
        except Exception as e:
            print(f"LLM解析失败: {e}")

    # 输出最终结果
    print(f"\n{'='*50}")
    print(f"[AutoOptimizer] 优化完成！")
    print(f"最优参数: {best_params}")
    print(
        f"最优结果: 夏普={best_result.get('sharpe'):.3f} 收益={best_result.get('total_return'):+.1f}% 交易={best_result.get('trade_count')}次"
    )

    return best_params, best_result


if __name__ == "__main__":
    import sys

    stock = sys.argv[1] if len(sys.argv) > 1 else "300236"
    start = sys.argv[2] if len(sys.argv) > 2 else "20240101"
    end = sys.argv[3] if len(sys.argv) > 3 else "20260531"
    rounds = int(sys.argv[4]) if len(sys.argv) > 4 else 10

    best_params, best_result = auto_optimize(
        stock_code=stock,
        start_date=start,
        end_date=end,
        max_rounds=rounds,
        target_sharpe=0.8,
    )
    if best_result:
        print(f"最优结果: 夏普={best_result.get('sharpe'):.3f} 收益={best_result.get('total_return'):+.1f}% 交易={best_result.get('trade_count')}次")
    else:
        print("所有轮次回测均失败，请检查策略名称和参数")

