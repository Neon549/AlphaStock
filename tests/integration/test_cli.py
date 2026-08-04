import unittest

from cli import build_parser


class CliParserTests(unittest.TestCase):
    def test_workflow_arguments_are_parsed_without_loading_runtime_services(self):
        args = build_parser().parse_args(
            ["workflow", "600519", "--strategy", "rsi", "--focus", "fundamental"]
        )
        self.assertEqual(args.command, "workflow")
        self.assertEqual(args.stock_code, "600519")
        self.assertEqual(args.strategy, "rsi")
        self.assertEqual(args.focus, "fundamental")

    def test_backtest_is_a_separate_command(self):
        args = build_parser().parse_args(["backtest", "600519"])
        self.assertEqual(args.command, "backtest")
        self.assertEqual(args.initial_cash, 100000.0)


if __name__ == "__main__":
    unittest.main()
