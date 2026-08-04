"""Configuration-only checks for the optional TechLens inference endpoint."""

import os
import unittest
from unittest.mock import patch

from config.llm_config import TechLensClient


class TechLensClientTests(unittest.TestCase):
    def test_reads_deployed_endpoint_and_timeouts_from_environment(self):
        with patch.dict(
            os.environ,
            {
                "TECHLENS_BASE_URL": "https://gpu.internal.example/techlens/",
                "TECHLENS_HEALTH_TIMEOUT_SECONDS": "1.5",
                "TECHLENS_REQUEST_TIMEOUT_SECONDS": "45",
            },
            clear=False,
        ):
            client = TechLensClient()

        self.assertEqual(client.base_url, "https://gpu.internal.example/techlens")
        self.assertEqual(client.health_timeout_seconds, 1.5)
        self.assertEqual(client.request_timeout_seconds, 45.0)


if __name__ == "__main__":
    unittest.main()
