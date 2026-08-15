"""Configuration isolation checks for the deterministic CI path."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_skip_dotenv_preserves_ci_environment_credentials_without_reading_local_file():
    """A checked-out .env must not override the deliberately fake CI secret."""
    env = os.environ.copy()
    env.update(
        {
            "ALPHASTOCK_SKIP_DOTENV": "1",
            "DEEPSEEK_API_KEY": "ci-test-placeholder",
            "DASHSCOPE_API_KEY": "ci-test-placeholder",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import config.llm_config as c; print(c.DEEPSEEK_API_KEY)",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    assert result.stdout.rstrip().endswith("ci-test-placeholder")
