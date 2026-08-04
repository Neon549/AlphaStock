#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory/long_term.py  ──  Agent 长期记忆（PostgreSQL 版）

表：trading_decisions / analysis_reflections / backtest_results
均由 db.init_db() 创建，此处只做读写。
"""

from datetime import datetime
from db import execute


class LongTermMemory:

    # ── 交易决策 ──────────────────────────────────────────────────────

    def save_decision(
        self,
        stock_code: str,
        decision: str,
        fundamental_summary: str = "",
        technical_summary: str = "",
        sentiment_summary: str = "",
    ):
        execute(
            """
            INSERT INTO trading_decisions
                (stock_code, analysis_date, decision,
                 fundamental_summary, technical_summary, sentiment_summary)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                stock_code,
                datetime.now().date(),
                decision[:500],
                fundamental_summary[:300],
                technical_summary[:300],
                sentiment_summary[:300],
            ),
        )
        print(f"💾 决策已保存到长期记忆")

    def get_history(self, stock_code: str, limit: int = 3) -> str:
        rows = execute(
            """
            SELECT analysis_date, decision
            FROM trading_decisions
            WHERE stock_code = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (stock_code, limit),
            fetch="all",
        )
        if not rows:
            return f"暂无 {stock_code} 的历史分析记录"

        history = [f"## {stock_code} 历史决策记录"]
        for date, decision in rows:
            history.append(f"\n### {date}\n{decision[:200]}...")
        return "\n".join(history)

    # ── 复盘反思 ──────────────────────────────────────────────────────

    def save_reflection(self, stock_code: str, reflection: str):
        execute(
            "INSERT INTO analysis_reflections (stock_code, reflection) VALUES (%s, %s)",
            (stock_code, reflection),
        )

    def get_reflections(self, stock_code: str) -> str:
        rows = execute(
            """
            SELECT reflection, created_at
            FROM analysis_reflections
            WHERE stock_code = %s
            ORDER BY created_at DESC
            LIMIT 3
            """,
            (stock_code,),
            fetch="all",
        )
        if not rows:
            return "暂无复盘记录"
        return "\n\n".join([f"[{str(r[1])[:10]}] {r[0]}" for r in rows])

    # ── 回测结果 ──────────────────────────────────────────────────────

    def save_backtest_result(self, stock_code: str, strategy: str, result_summary: str):
        execute(
            """
            INSERT INTO backtest_results (stock_code, strategy, result_summary)
            VALUES (%s, %s, %s)
            """,
            (stock_code, strategy, result_summary[:500]),
        )
        print(f"💾 回测结果已保存: {stock_code} - {strategy}")

    def get_backtest_history(self, stock_code: str, limit: int = 5) -> str:
        rows = execute(
            """
            SELECT strategy, result_summary, created_at
            FROM backtest_results
            WHERE stock_code = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (stock_code, limit),
            fetch="all",
        )
        if not rows:
            return f"暂无 {stock_code} 的回测记录"

        history = [f"## {stock_code} 历史回测记录"]
        for strategy, summary, created_at in rows:
            history.append(f"\n### {str(created_at)[:10]} - {strategy}\n{summary}")
        return "\n".join(history)
