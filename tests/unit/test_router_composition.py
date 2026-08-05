import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import router


class RouterCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        cls.client = TestClient(app)

    def test_public_research_and_backtest_routes_are_composed(self):
        self.assertEqual(self.client.get("/api/v1/skills").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/backtest/strategies").status_code, 200)

    def test_protected_routers_keep_existing_auth_boundaries(self):
        self.assertEqual(self.client.get("/api/v1/memory/preferences").status_code, 422)
        self.assertEqual(self.client.get("/api/v1/conversations/tester").status_code, 401)


if __name__ == "__main__":
    unittest.main()
