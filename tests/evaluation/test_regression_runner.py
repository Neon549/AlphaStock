import unittest

from evaluation.regression_runner import run


class WorkflowRegressionTests(unittest.TestCase):
    def test_all_committed_workflow_cases_pass(self):
        total, failures = run()
        self.assertGreater(total, 0)
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
