import unittest
from starlette.testclient import TestClient
from api.app import app

class TestFastAPIApp(unittest.TestCase):
    def setUp(self):
        import asyncio
        from database.repository import DatabaseRepository
        from config.settings import settings
        asyncio.run(DatabaseRepository().init_db())
        headers = {"X-API-Key": settings.DASHBOARD_API_KEY} if settings.DASHBOARD_API_KEY else {}
        self.client = TestClient(app, headers=headers)

    def test_dashboard_root(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("NexusTrader", response.text)

    def test_get_bot_status(self):
        response = self.client.get("/api/v1/bot/status")
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertEqual(json_data["status"], "success")
        self.assertIn("data", json_data)

    def test_get_risk_config(self):
        response = self.client.get("/api/v1/config/risk")
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertEqual(json_data["status"], "success")

    def test_update_risk_config(self):
        payload = {
            "stop_loss_daily": 60.0,
            "take_profit_daily": 120.0,
            "max_daily_trades": 30,
            "max_single_stake": 15.0,
            "max_consecutive_losses": 4,
            "cooldown_minutes": 20
        }
        response = self.client.post("/api/v1/config/risk", json=payload)
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertEqual(json_data["status"], "success")
        self.assertEqual(json_data["data"]["stop_loss_daily"], 60.0)

    def test_get_trades_stats(self):
        response = self.client.get("/api/v1/trades/stats")
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertEqual(json_data["status"], "success")

    def test_list_trades(self):
        response = self.client.get("/api/v1/trades/list?limit=5")
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertEqual(json_data["status"], "success")

if __name__ == "__main__":
    unittest.main()
