#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
@author: yulin
@created: 2026/5/29 21:12
@updated: 2026/5/29 21:12
@version: 1.0
@description:
"""

import backtrader as bt
import pandas as pd
from config.runtime_paths import REPORTS_DIR
from backtest.strategies import STRATEGY_MAP

REPORT_DIR = REPORTS_DIR / "backtest"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def run_backtest(
    df: pd.DataFrame,
    strategy_name: str = "kdj_macd",
    initial_cash: float = 100_000.0,
    commission: float = 0.001,
    printlog: bool = False,
    strategy_params: dict = None,  # 新增
    generate_html_report: bool = True,
) -> dict:
    strategy_cls = STRATEGY_MAP.get(strategy_name)
    if strategy_cls is None:
        raise ValueError(
            f"未知策略: {strategy_name}，可选: {list(STRATEGY_MAP.keys())}"
        )

    cerebro = bt.Cerebro()
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=commission)

    data_feed = bt.feeds.PandasData(
        dataname=df,
        datetime=None,
        open="open",
        high="high",
        low="low",
        close="close",
        volume="volume",
        openinterest=-1,
    )
    cerebro.adddata(data_feed)
    cerebro.addsizer(bt.sizers.PercentSizer, percents=95)
    strategy_params = strategy_params or {}
    strategy_params["printlog"] = printlog
    cerebro.addstrategy(strategy_cls, **strategy_params)

    cerebro.addanalyzer(
        bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.03, annualize=True
    )
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.TimeReturn, _name="time_return")
    cerebro.addanalyzer(bt.analyzers.Transactions, _name="transactions")

    start_value = cerebro.broker.getvalue()
    results = cerebro.run()
    end_value = cerebro.broker.getvalue()
    strat = results[0]

    sharpe_raw = strat.analyzers.sharpe.get_analysis()
    sharpe_val = sharpe_raw.get("sharperatio", None)

    drawdown_raw = strat.analyzers.drawdown.get_analysis()
    max_dd = drawdown_raw.get("max", {}).get("drawdown", 0.0)

    trade_raw = strat.analyzers.trades.get_analysis()
    total_trades = trade_raw.get("total", {}).get("total", 0)
    won_trades = trade_raw.get("won", {}).get("total", 0)

    time_return = strat.analyzers.time_return.get_analysis()
    # 提取交易明细
    transactions_raw = strat.analyzers.transactions.get_analysis()
    trade_records = []
    for date, trades in transactions_raw.items():
        for trade in trades:
            trade_records.append(
                {
                    "date": str(date.date()),
                    "size": trade[0],  # 正数=买入 负数=卖出
                    "price": round(trade[1], 2),
                    "type": "买入" if trade[0] > 0 else "卖出",
                }
            )
    returns_series = pd.Series(time_return).sort_index()
    returns_series.index = pd.to_datetime(returns_series.index)

    total_return_pct = (end_value - start_value) / start_value * 100

    report_path = None
    if generate_html_report:
        report_path = REPORT_DIR / f"{strategy_name}_report.html"
        try:
            import quantstats as qs

            qs.reports.html(
                returns_series,
                output=str(report_path),
                title=f"A股回测报告 - {strategy_name.upper()}策略",
                download_filename=str(report_path),
            )
            print(f"[Engine] 报告生成: {report_path}")
        except Exception as e:
            print(f"[Engine] 报告生成失败: {e}")
            report_path = None

    return {
        "strategy": strategy_name,
        "initial_cash": initial_cash,
        "final_value": round(end_value, 2),
        "total_return": round(total_return_pct, 2),
        "sharpe": round(sharpe_val, 3) if sharpe_val else None,
        "max_drawdown": round(max_dd, 2),
        "trade_count": total_trades,
        "win_trades": won_trades,
        "win_rate": (
            round(won_trades / total_trades * 100, 1) if total_trades > 0 else 0
        ),
        "returns_series": returns_series,
        "report_path": str(report_path) if report_path else None,
        "trade_records": trade_records,
    }


def format_result(result: dict) -> str:
    lines = [
        f"## 回测报告 - {result['strategy'].upper()}策略",
        f"",
        f"| 指标 | 值 |",
        f"|------|------|",
        f"| 初始资金 | ¥{result['initial_cash']:,.0f} |",
        f"| 最终资产 | ¥{result['final_value']:,.0f} |",
        f"| 总收益率 | {result['total_return']:+.2f}% |",
        f"| 夏普比率 | {result['sharpe']} |",
        f"| 最大回撤 | -{result['max_drawdown']:.2f}% |",
        f"| 交易次数 | {result['trade_count']} |",
        f"| 胜率 | {result['win_rate']}% |",
    ]
    return "\n".join(lines)
