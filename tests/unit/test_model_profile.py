import unittest
from unittest.mock import patch

from control_plane.model_profile import ModelProfile, active_model, model_scope


class ModelProfileTests(unittest.TestCase):
    def test_scope_isolated_and_restored(self):
        first = ModelProfile("fast", quick="fast-quick", deep="fast-deep", planner="fast-planner")
        second = ModelProfile("strong", quick="strong-quick", deep="strong-deep", planner="strong-planner")
        with patch("control_plane.model_profile.build_model_profile", side_effect=[first, second]):
            with model_scope("fast"):
                self.assertEqual(active_model("quick"), "fast-quick")
                self.assertEqual(active_model("planner"), "fast-planner")
                with model_scope("strong"):
                    self.assertEqual(active_model("deep"), "strong-deep")
                    self.assertEqual(active_model("planner"), "strong-planner")
                self.assertEqual(active_model("quick"), "fast-quick")
        self.assertIsNone(active_model("quick"))
