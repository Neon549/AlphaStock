#!/usr/bin/env python3
"""Local, scriptable entrypoints for AlphaStock.

Examples:
  python cli.py analyze 600519
  python cli.py backtest 600519 --strategy kdj_macd --start-date 20230101 --end-date 20251231
  python cli.py workflow 600519 --strategy kdj_macd --output report.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _common_arguments(parser: argparse.ArgumentParser, *, include_focus: bool = False) -> None:
    parser.add_argument("stock_code", help="six-digit A-share code, e.g. 600519")
    if include_focus:
        parser.add_argument(
            "--focus",
            choices=["all", "technical", "fundamental", "sentiment"],
            default="all",
            help="limit the analyst branches (default: all)",
        )


def _backtest_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--strategy", default="kdj_macd")
    parser.add_argument("--start-date", default="20220101")
    parser.add_argument("--end-date", default="20261231")
    parser.add_argument("--initial-cash", type=float, default=100000.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AlphaStock local workflow CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="run the bounded analyst workflow")
    _common_arguments(analyze, include_focus=True)
    analyze.add_argument("--model", choices=["fast", "smart", "strong"], default="smart")

    backtest = subparsers.add_parser("backtest", help="run a standalone quant backtest")
    _common_arguments(backtest)
    _backtest_arguments(backtest)

    workflow = subparsers.add_parser(
        "workflow", help="run analysis and backtest in parallel, then create a reviewable draft"
    )
    _common_arguments(workflow, include_focus=True)
    _backtest_arguments(workflow)
    workflow.add_argument("--output", type=Path, help="optional JSON report path")
    return parser


def _emit(result: dict[str, Any], output: Path | None = None) -> None:
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(text)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        print(f"Saved report: {output}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "analyze":
        from control_plane.gateway import Gateway
        from control_plane.investment_runtime import InvestmentRuntime
        from control_plane.run_store import PostgresRunStore
        from control_plane.triggers import cli_event

        event = cli_event(
            f"分析 {args.stock_code}",
            model=args.model,
        )
        run = Gateway(InvestmentRuntime(), store=PostgresRunStore()).dispatch(event)
        _emit({"run_id": run.run_id, "route": run.route, **run.payload})
        return 0

    if args.command == "backtest":
        from agent_runtime.workflows.runtime import PythonBacktestRuntime

        _emit(
            PythonBacktestRuntime().run(
                args.stock_code,
                strategy=args.strategy,
                start_date=args.start_date,
                end_date=args.end_date,
                initial_cash=args.initial_cash,
            )
        )
        return 0

    from agent_runtime.workflows.investment_workflow import run_local_investment_workflow

    _emit(
        run_local_investment_workflow(
            args.stock_code,
            strategy=args.strategy,
            start_date=args.start_date,
            end_date=args.end_date,
            initial_cash=args.initial_cash,
            analyst_focus=args.focus,
        ),
        args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
