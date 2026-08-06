import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.live_store import LiveStore
from config.settings import settings
from database.repository import DatabaseRepository


class LiveStoreMarketContextTests(unittest.TestCase):
    def test_tick_from_previous_symbol_is_ignored_after_new_history(self):
        store = LiveStore()
        store.apply({
            "event_id": "history-r75", "type": "market.history", "bot_id": "bot-a",
            "epoch": 100, "symbol": "R_75", "timeframe_seconds": 1,
            "mode": "line", "points": [{"time": 100, "value": 51000}],
        })

        accepted = store.apply({
            "event_id": "stale-r50", "type": "market.tick", "bot_id": "bot-a",
            "epoch": 101, "symbol": "R_50", "timeframe_seconds": 1, "price": 200,
        })

        snapshot = store.snapshot("bot-a")
        self.assertFalse(accepted)
        self.assertIsNone(snapshot["last_tick"])
        self.assertEqual(snapshot["market"]["points"], [{"time": 100, "value": 51000}])

    def test_tick_from_previous_timeframe_is_ignored(self):
        store = LiveStore()
        store.apply({
            "event_id": "history-1m", "type": "market.history", "bot_id": "bot-a",
            "epoch": 100, "symbol": "R_75", "timeframe_seconds": 60,
            "mode": "candles", "points": [],
        })

        accepted = store.apply({
            "event_id": "stale-5m", "type": "market.tick", "bot_id": "bot-a",
            "epoch": 101, "symbol": "R_75", "timeframe_seconds": 300, "price": 51000,
            "candle": {"time": 0, "open": 51000, "high": 51000, "low": 51000, "close": 51000},
        })

        self.assertFalse(accepted)
        self.assertIsNone(store.snapshot("bot-a")["last_tick"])


class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repository = DatabaseRepository(str(Path(self.tempdir.name) / "control.db"))
        self.live_store = LiveStore()
        self.client_context = TestClient(create_app(self.repository, self.live_store))
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_bot_start_and_stop_persist_desired_state(self):
        created = self.client.post("/api/v1/bots", json={
            "name": "BB 1m",
            "account_id": "VRTC100",
            "account_type": "demo",
            "symbol": "R_100",
        })
        self.assertEqual(created.status_code, 201)
        bot_id = created.json()["data"]["id"]

        started = self.client.post(f"/api/v1/bots/{bot_id}/start")
        stopped = self.client.post(f"/api/v1/bots/{bot_id}/stop")

        self.assertEqual(started.json()["data"]["desired_state"], "RUNNING")
        self.assertEqual(stopped.json()["data"]["desired_state"], "STOPPED")

    def test_health_endpoint_reports_database_and_control_plane_ready(self):
        response = self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})

    def test_account_catalog_returns_normalized_real_and_demo_accounts(self):
        async def account_provider():
            return [
                {"account_id": "ROT100", "account_type": "real", "balance": "0.00", "currency": "USD", "status": "active"},
                {"account_id": "DOT200", "account_type": "demo", "balance": "1000.00", "currency": "USD", "status": "active"},
            ]

        with TestClient(create_app(self.repository, self.live_store, account_provider=account_provider)) as client:
            response = client.get("/api/v1/accounts")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"][0]["account_type"], "real")
        self.assertEqual(response.json()["data"][1]["balance"], 1000.0)

    def test_real_account_configuration_is_rejected(self):
        response = self.client.post("/api/v1/bots", json={
            "name": "Conta real",
            "account_id": "CR100",
            "account_type": "real",
            "symbol": "R_100",
        })
        self.assertEqual(response.status_code, 422)

    def test_internal_events_require_internal_token_and_feed_bot_snapshot(self):
        previous = settings.INTERNAL_API_TOKEN
        settings.INTERNAL_API_TOKEN = "internal-secret"
        try:
            event = {
                "event_id": "evt-1",
                "schema_version": 1,
                "type": "market.tick",
                "bot_id": "bot-a",
                "epoch": 123,
                "price": 100.5,
            }
            denied = self.client.post("/api/v1/internal/events", json=event)
            accepted = self.client.post(
                "/api/v1/internal/events",
                json=event,
                headers={"X-Internal-Token": "internal-secret"},
            )
            snapshot = self.client.get("/api/v1/bots/bot-a/snapshot")
        finally:
            settings.INTERNAL_API_TOKEN = previous

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(snapshot.json()["data"]["last_tick"]["price"], 100.5)

    def test_duplicate_event_is_idempotent(self):
        event = {
            "event_id": "evt-same",
            "schema_version": 1,
            "type": "trade.closed",
            "bot_id": "bot-a",
            "epoch": 123,
            "trade": {"contract_id": 42, "profit": 0.9},
        }
        first = self.client.post("/api/v1/internal/events", json=event)
        second = self.client.post("/api/v1/internal/events", json=event)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.json()["duplicate"], True)
        self.assertEqual(len(self.live_store.snapshot("bot-a")["recent_trades"]), 1)


if __name__ == "__main__":
    unittest.main()
