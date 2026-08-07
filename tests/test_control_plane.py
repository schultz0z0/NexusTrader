import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.app import create_app
from api.live_store import LiveStore
from config.settings import settings
from database.repository import DatabaseRepository


class LiveStoreMarketContextTests(unittest.TestCase):
    def test_runtime_restart_does_not_erase_closed_trade_history(self):
        store = LiveStore()
        store.apply({
            "event_id": "closed-1", "type": "trade.closed", "bot_id": "bot-a",
            "epoch": 100, "trade": {"contract_id": 42, "status": "closed", "profit": 0.8},
        })

        store.apply({
            "event_id": "starting-1", "type": "runtime.status", "bot_id": "bot-a",
            "epoch": 101, "status": "STARTING",
        })

        self.assertEqual(
            store.snapshot("bot-a")["recent_trades"],
            [{"contract_id": 42, "status": "closed", "profit": 0.8}],
        )

    def test_first_tick_rebuilds_market_context_after_api_restart(self):
        store = LiveStore()

        accepted = store.apply({
            "event_id": "tick-after-restart", "type": "market.tick", "bot_id": "bot-a",
            "epoch": 181, "symbol": "R_75", "timeframe_seconds": 60, "price": 12.5,
            "candle": {"time": 180, "open": 11, "high": 13, "low": 10, "close": 12.5},
            "bollinger": {"upper": 15, "middle": 12, "lower": 9},
            "zigzag": [{"time": 120, "value": 13}, {"time": 180, "value": 10}],
        })

        market = store.snapshot("bot-a")["market"]
        self.assertTrue(accepted)
        self.assertEqual(market["symbol"], "R_75")
        self.assertEqual(market["timeframe_seconds"], 60)
        self.assertEqual(market["points"], [{"time": 180, "open": 11, "high": 13, "low": 10, "close": 12.5}])
        self.assertEqual(market["donchian"]["upper"], [{"time": 180, "value": 15}])
        self.assertEqual(market["zigzag"], [{"time": 120, "value": 13}, {"time": 180, "value": 10}])

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
        headers = {"X-API-Key": settings.DASHBOARD_API_KEY} if settings.DASHBOARD_API_KEY else {}
        self.client_context = TestClient(create_app(self.repository, self.live_store), headers=headers)
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

    def test_server_side_stop_all_persists_emergency_stop_for_every_bot(self):
        first = self.client.post("/api/v1/bots", json={
            "name": "First", "account_id": "DOT100", "account_type": "demo",
        }).json()["data"]
        second = self.client.post("/api/v1/bots", json={
            "name": "Second", "account_id": "DOT100", "account_type": "demo",
        }).json()["data"]
        self.client.post(f"/api/v1/bots/{first['id']}/start")
        self.client.post(f"/api/v1/bots/{second['id']}/start")

        stopped = self.client.post("/api/v1/bots/stop-all")

        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(stopped.json()["data"]["stopped"], 2)
        states = {item["id"]: item["desired_state"] for item in self.client.get("/api/v1/bots").json()["data"]}
        self.assertEqual(states[first["id"]], "STOPPED")
        self.assertEqual(states[second["id"]], "STOPPED")

    def test_health_endpoint_reports_database_and_control_plane_ready(self):
        response = self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(response.json()["checks"]["database"], "ok")

    def test_liveness_does_not_depend_on_external_services(self):
        response = self.client.get("/api/v1/health/live")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "alive"})

    def test_readiness_fails_closed_for_running_bot_without_fresh_runtime_health(self):
        created = self.client.post("/api/v1/bots", json={
            "name": "Running health", "account_id": "DOT100",
            "account_type": "demo", "symbol": "R_75",
        }).json()["data"]
        self.client.post(f"/api/v1/bots/{created['id']}/start")

        response = self.client.get("/api/v1/health/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")
        self.assertIn(created["id"], response.json()["checks"]["bots"])

    def test_account_catalog_returns_normalized_real_and_demo_accounts(self):
        async def account_provider():
            return [
                {"account_id": "ROT100", "account_type": "real", "balance": "0.00", "currency": "USD", "status": "active"},
                {"account_id": "DOT200", "account_type": "demo", "balance": "1000.00", "currency": "USD", "status": "active"},
            ]

        headers = {"X-API-Key": settings.DASHBOARD_API_KEY} if settings.DASHBOARD_API_KEY else {}
        with TestClient(create_app(self.repository, self.live_store, account_provider=account_provider), headers=headers) as client:
            response = client.get("/api/v1/accounts")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"][0]["account_type"], "real")
        self.assertEqual(response.json()["data"][1]["balance"], 1000.0)

    def test_real_account_can_be_selected_while_execution_is_disabled(self):
        previous = settings.ALLOW_REAL_TRADING
        previous_cap = settings.REAL_MAX_STAKE_USD
        settings.ALLOW_REAL_TRADING = False
        settings.REAL_MAX_STAKE_USD = 0.0
        try:
            configured = self.client.post("/api/v1/bots", json={
                "name": "Conta real",
                "account_id": "CR100",
                "account_type": "real",
                "symbol": "R_100",
            })
            started = self.client.post(
                f"/api/v1/bots/{configured.json()['data']['id']}/start"
            ) if configured.status_code == 201 else None
        finally:
            settings.ALLOW_REAL_TRADING = previous
            settings.REAL_MAX_STAKE_USD = previous_cap

        self.assertEqual(configured.status_code, 201)
        self.assertEqual(configured.json()["data"]["account_type"], "real")
        self.assertEqual(started.status_code, 403)

    def test_real_start_requires_exact_server_side_confirmation_ticket(self):
        previous_allow = settings.ALLOW_REAL_TRADING
        previous_cap = settings.REAL_MAX_STAKE_USD
        settings.ALLOW_REAL_TRADING = True
        settings.REAL_MAX_STAKE_USD = 5.0
        try:
            created = self.client.post("/api/v1/bots", json={
                "name": "Conta real protegida", "account_id": "CR100",
                "account_type": "real", "symbol": "R_75", "initial_stake": 1.0,
            }).json()["data"]

            denied = self.client.post(f"/api/v1/bots/{created['id']}/start")
            wrong = self.client.post(
                f"/api/v1/bots/{created['id']}/real-confirmation",
                json={"phrase": "SIM"},
            )
            issued = self.client.post(
                f"/api/v1/bots/{created['id']}/real-confirmation",
                json={"phrase": "REAL CR100"},
            )
            ticket = issued.json()["data"]["ticket"]
            started = self.client.post(
                f"/api/v1/bots/{created['id']}/start",
                json={"real_ticket": ticket},
            )
        finally:
            settings.ALLOW_REAL_TRADING = previous_allow
            settings.REAL_MAX_STAKE_USD = previous_cap

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(wrong.status_code, 422)
        self.assertEqual(issued.status_code, 200)
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["data"]["desired_state"], "RUNNING")

    def test_real_stake_above_server_cap_is_rejected_on_start(self):
        previous_allow = settings.ALLOW_REAL_TRADING
        previous_cap = settings.REAL_MAX_STAKE_USD
        settings.ALLOW_REAL_TRADING = True
        settings.REAL_MAX_STAKE_USD = 2.0
        try:
            configured = self.client.post("/api/v1/bots", json={
                "name": "Stake real excessiva", "account_id": "CR100",
                "account_type": "real", "symbol": "R_75", "initial_stake": 2.01,
            })
            response = self.client.post(
                f"/api/v1/bots/{configured.json()['data']['id']}/start"
            ) if configured.status_code == 201 else configured
        finally:
            settings.ALLOW_REAL_TRADING = previous_allow
            settings.REAL_MAX_STAKE_USD = previous_cap

        self.assertEqual(configured.status_code, 201)
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
        previous = settings.INTERNAL_API_TOKEN
        settings.INTERNAL_API_TOKEN = "internal-secret"
        try:
            event = {
                "event_id": "evt-same",
                "schema_version": 1,
                "type": "trade.closed",
                "bot_id": "bot-a",
                "epoch": 123,
                "trade": {"contract_id": 42, "profit": 0.9},
            }
            headers = {"X-Internal-Token": "internal-secret"}
            first = self.client.post("/api/v1/internal/events", json=event, headers=headers)
            second = self.client.post("/api/v1/internal/events", json=event, headers=headers)
        finally:
            settings.INTERNAL_API_TOKEN = previous

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.json()["duplicate"], True)
        self.assertEqual(len(self.live_store.snapshot("bot-a")["recent_trades"]), 1)

    def test_websocket_uses_one_time_ticket_instead_of_dashboard_key_in_url(self):
        issued = self.client.post("/api/v1/ws-tickets/bot-a")
        ticket = issued.json()["data"]["ticket"]

        with self.client.websocket_connect(f"/api/v1/ws/bots/bot-a?ticket={ticket}") as websocket:
            snapshot = websocket.receive_json()

        self.assertEqual(snapshot["type"], "snapshot")
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(f"/api/v1/ws/bots/bot-a?ticket={ticket}"):
                pass


if __name__ == "__main__":
    unittest.main()
