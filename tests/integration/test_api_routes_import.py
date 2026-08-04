"""Import-level contract for every FastAPI business route.

FastAPI validates multipart parameters when a route is declared, so a missing
runtime dependency can otherwise leave the service alive but without its
business router.  Keep that failure in CI instead of discovering it as a 404.
"""

import unittest


class ApiRoutesImportTests(unittest.TestCase):
    def test_business_router_imports_with_declared_dependencies(self):
        from api.routes import router

        self.assertGreater(len(router.routes), 1)


if __name__ == "__main__":
    unittest.main()
