import unittest

from config.runtime_paths import (
    CACHE_DIR,
    CHECKPOINT_DB_PATH,
    PROJECT_ROOT,
    REPORTS_DIR,
    STOCK_UNIVERSE_CACHE_FILE,
    TMP_DIR,
)


class RuntimePathTests(unittest.TestCase):
    def test_runtime_artifacts_are_kept_under_project_runtime_directory(self):
        runtime = PROJECT_ROOT / "runtime"
        for path in (CACHE_DIR, REPORTS_DIR, TMP_DIR, CHECKPOINT_DB_PATH, STOCK_UNIVERSE_CACHE_FILE):
            self.assertTrue(path.is_relative_to(runtime))
