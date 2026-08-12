import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.live_store import LiveStore
from config.settings import settings
from database.repository import DatabaseRepository
from core.events import is_critical_event
from nexus_trade.domain import Lane
from nexus_trade.reports import ReportService
from nexus_trade.scheduler import BrasiliaSchedule
from tests.test_nexus_trade_reports import report_evidence


class NexusTradeApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repository = DatabaseRepository(
            str(Path(self.tempdir.name) / "nexus-control.db"),
        )
        self.live_store = LiveStore()
        self.headers = {"X-API-Key": settings.DASHBOARD_API_KEY}
        self.client_context = TestClient(
            create_app(self.repository, self.live_store),
            headers=self.headers,
        )
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_nexus_control_plane_requires_dashboard_authentication(self):
        response = self.client.get(
            "/api/v1/nexus-trade",
            headers={"X-API-Key": "wrong-key"},
        )

        self.assertEqual(response.status_code, 401)

    def test_initial_snapshot_is_versioned_and_contains_both_lanes(self):
        response = self.client.get("/api/v1/nexus-trade")

        self.assertEqual(response.status_code, 200)
        snapshot = response.json()["data"]
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertGreaterEqual(snapshot["snapshot_version"], 1)
        self.assertEqual(snapshot["bot_id"], "nexus-trade")
        self.assertEqual(snapshot["runtime"]["champion_enabled"], 0)
        self.assertFalse(snapshot["emergency_stop"])
        self.assertEqual(
            {item["lane"] for item in snapshot["lanes"]},
            {Lane.CHAMPION.value, Lane.TRIAL.value},
        )
        self.assertEqual(snapshot["lanes"][0]["version"]["status"], "CHAMPION")
        self.assertTrue(snapshot["active_campaigns"])
        self.assertEqual(
            set(snapshot["learning"]),
            {"jobs", "attempts", "candidates"},
        )
        self.assertEqual(snapshot["learning"]["jobs"], [])
        self.assertEqual(snapshot["learning"]["attempts"], [])
        self.assertEqual(snapshot["learning"]["candidates"][0]["status"], "TRIAL")
        self.assertEqual(snapshot["decisions"], [])
        self.assertEqual(snapshot["trades"], [])

    def test_mode_persists_off_demo_and_on_demo_for_runtime_refresh(self):
        off = self.client.post(
            "/api/v1/nexus-trade/mode",
            json={
                "enabled": False,
                "account_id": "DOT-DEMO",
                "account_type": "demo",
            },
        )
        on = self.client.post(
            "/api/v1/nexus-trade/mode",
            json={
                "enabled": True,
                "account_id": "DOT-DEMO",
                "account_type": "demo",
            },
        )

        self.assertEqual(off.status_code, 200)
        self.assertEqual(off.json()["data"]["runtime"]["champion_enabled"], 0)
        self.assertEqual(on.status_code, 200)
        runtime = on.json()["data"]["runtime"]
        self.assertEqual(runtime["champion_enabled"], 1)
        self.assertEqual(runtime["champion_account_id"], "DOT-DEMO")
        self.assertEqual(runtime["champion_account_type"], "demo")
        persisted = self.client.get("/api/v1/nexus-trade").json()["data"]
        self.assertEqual(persisted["runtime"], runtime)
        self.assertEqual(
            self.live_store.snapshot("nexus-trade")["last_nexus_event"]["type"],
            "nexus.runtime",
        )

    def test_champion_management_route_persists_and_rejects_stale_revision(self):
        payload = {
            "expected_revision": 1,
            "initial_stake": 0.7,
            "money_management": "soros",
            "money_config": {"levels": 2, "percent": 0.5},
            "risk_config": {
                "take_profit_daily": 15,
                "stop_loss_daily": 8,
                "max_daily_trades": 24,
                "max_single_stake": 5,
                "max_consecutive_losses": 3,
                "cooldown_minutes": 10,
            },
        }

        response = self.client.post(
            "/api/v1/nexus-trade/champion-management",
            json=payload,
        )

        self.assertEqual(response.status_code, 200)
        snapshot = response.json()["data"]
        self.assertEqual(snapshot["champion_management"]["revision"], 2)
        self.assertEqual(snapshot["champion_management"]["money_management"], "soros")
        self.assertEqual(
            self.live_store.snapshot("nexus-trade")["last_nexus_event"]["type"],
            "nexus.runtime",
        )

        stale = self.client.post(
            "/api/v1/nexus-trade/champion-management",
            json={**payload, "initial_stake": 0.9},
        )
        self.assertEqual(stale.status_code, 409)
        persisted = self.client.get("/api/v1/nexus-trade").json()["data"]
        self.assertEqual(persisted["champion_management"], snapshot["champion_management"])

    def test_champion_management_route_rejects_invalid_or_unsafe_payload(self):
        base = {
            "expected_revision": 1,
            "initial_stake": 0.7,
            "money_management": "fixed",
            "money_config": {},
            "risk_config": {},
        }
        invalid = (
            {**base, "initial_stake": -1},
            {**base, "money_management": "unknown"},
            {**base, "money_config": {"multiplier": 2}},
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/api/v1/nexus-trade/champion-management",
                    json=payload,
                )
                self.assertEqual(response.status_code, 422)

        started = self.client.post(
            "/api/v1/nexus-trade/mode",
            json={"enabled": True, "account_id": "DOT-DEMO", "account_type": "demo"},
        )
        self.assertEqual(started.status_code, 200)
        unsafe = self.client.post(
            "/api/v1/nexus-trade/champion-management",
            json=base,
        )
        self.assertEqual(unsafe.status_code, 409)

    def test_real_confirmation_uses_persisted_champion_initial_stake(self):
        configured = self.client.post(
            "/api/v1/nexus-trade/champion-management",
            json={
                "expected_revision": 1,
                "initial_stake": 0.7,
                "money_management": "fixed",
                "money_config": {},
                "risk_config": {},
            },
        )
        self.assertEqual(configured.status_code, 200)
        previous_allow = settings.ALLOW_REAL_TRADING
        previous_cap = settings.REAL_MAX_STAKE_USD
        settings.ALLOW_REAL_TRADING = True
        settings.REAL_MAX_STAKE_USD = 0.5
        try:
            response = self.client.post(
                "/api/v1/nexus-trade/real-confirmation",
                json={"account_id": "ROT-REAL", "phrase": "REAL ROT-REAL"},
            )
        finally:
            settings.ALLOW_REAL_TRADING = previous_allow
            settings.REAL_MAX_STAKE_USD = previous_cap

        self.assertEqual(response.status_code, 422)

    def test_real_mode_remains_fail_closed_without_server_flag_or_ticket(self):
        previous_allow = settings.ALLOW_REAL_TRADING
        previous_cap = settings.REAL_MAX_STAKE_USD
        settings.ALLOW_REAL_TRADING = False
        settings.REAL_MAX_STAKE_USD = 0.0
        try:
            response = self.client.post(
                "/api/v1/nexus-trade/mode",
                json={
                    "enabled": True,
                    "account_id": "ROT-REAL",
                    "account_type": "real",
                    "real_ticket": "untrusted-client-value",
                },
            )
        finally:
            settings.ALLOW_REAL_TRADING = previous_allow
            settings.REAL_MAX_STAKE_USD = previous_cap

        self.assertEqual(response.status_code, 403)
        runtime = self.client.get("/api/v1/nexus-trade").json()["data"]["runtime"]
        self.assertEqual(runtime["champion_enabled"], 0)
        self.assertEqual(runtime["champion_account_type"], "demo")

    def test_emergency_stop_is_durable_and_published_to_the_snapshot(self):
        stopped = self.client.post(
            "/api/v1/nexus-trade/emergency-stop",
            json={"enabled": True},
        )

        self.assertEqual(stopped.status_code, 200)
        snapshot = stopped.json()["data"]
        self.assertTrue(snapshot["emergency_stop"])
        self.assertTrue(snapshot["runtime"]["emergency_stop"])
        self.assertEqual(snapshot["last_nexus_event"]["type"], "nexus.runtime")
        persisted = self.client.get("/api/v1/nexus-trade").json()["data"]
        self.assertTrue(persisted["emergency_stop"])

    def test_websocket_snapshot_contains_durable_nexus_state_without_tickets(self):
        self.client.post(
            "/api/v1/nexus-trade/mode",
            json={
                "enabled": True,
                "account_id": "DOT-DEMO",
                "account_type": "demo",
            },
        )
        issued = self.client.post("/api/v1/ws-tickets/nexus-trade")
        ticket = issued.json()["data"]["ticket"]

        with self.client.websocket_connect(
            f"/api/v1/ws/bots/nexus-trade?ticket={ticket}",
        ) as websocket:
            message = websocket.receive_json()

        snapshot = message["data"]
        self.assertEqual(message["type"], "snapshot")
        self.assertEqual(snapshot["runtime"]["champion_enabled"], 1)
        self.assertEqual(len(snapshot["lanes"]), 2)
        serialized = json.dumps(snapshot).lower()
        self.assertNotIn("real_ticket", serialized)
        self.assertNotIn("ws-ticket", serialized)
        self.assertNotIn(ticket.lower(), serialized)

    def test_future_read_endpoints_return_only_persisted_data(self):
        expected_nonempty = {
            "versions": True,
            "campaigns": True,
            "reports": False,
            "proposals": False,
            "exports": False,
        }

        for endpoint, should_have_rows in expected_nonempty.items():
            with self.subTest(endpoint=endpoint):
                response = self.client.get(f"/api/v1/nexus-trade/{endpoint}")
                self.assertEqual(response.status_code, 200)
                data = response.json()["data"]
                self.assertIsInstance(data, list)
                self.assertEqual(bool(data), should_have_rows)
                self.assertNotIn("path", json.dumps(data).lower())

    def test_report_history_and_safe_exports_are_thin_service_results(self):
        service = ReportService(self.repository.db_path)
        window = BrasiliaSchedule().weekly_window(
            datetime(2026, 8, 10, 13, tzinfo=timezone.utc)
        )
        report = service.close_weekly(window, report_evidence())

        detail = self.client.get(f"/api/v1/nexus-trade/reports/{report.id}")
        historical = self.client.get("/api/v1/nexus-trade/reports/weekly/2026-08-10")
        csv_export = self.client.get(
            f"/api/v1/nexus-trade/reports/{report.id}/exports/csv.zip"
        )
        xlsx_export = self.client.get(
            f"/api/v1/nexus-trade/reports/{report.id}/exports/xlsx"
        )
        exports = self.client.get("/api/v1/nexus-trade/exports")

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["report_hash"], report.report_hash)
        self.assertEqual(historical.json()["data"]["id"], report.id)
        self.assertEqual(csv_export.status_code, 200)
        self.assertEqual(csv_export.headers["content-type"], "application/zip")
        self.assertTrue(csv_export.content.startswith(b"PK"))
        self.assertEqual(xlsx_export.status_code, 200)
        self.assertTrue(xlsx_export.content.startswith(b"PK"))
        serialized = json.dumps(exports.json()).lower()
        self.assertIn(report.report_hash, serialized)
        self.assertNotIn("path", serialized)
        self.assertNotIn("token", serialized)

    def test_internal_ingress_reports_incomplete_nexus_envelope_as_rejected(self):
        previous = settings.INTERNAL_API_TOKEN
        settings.INTERNAL_API_TOKEN = "dummy-internal"
        try:
            response = self.client.post(
                "/api/v1/internal/events",
                json={
                    "event_id": "missing-revision",
                    "schema_version": 1,
                    "type": "nexus.decision",
                    "bot_id": "nexus-trade",
                    "payload": {"id": "decision-invalid"},
                },
                headers={"X-Internal-Token": "dummy-internal"},
            )
        finally:
            settings.INTERNAL_API_TOKEN = previous

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["accepted"], False)
        self.assertEqual(
            self.live_store.snapshot("nexus-trade")["nexus_events"],
            [],
        )


class NexusLiveStoreTests(unittest.TestCase):
    def test_incomplete_or_duplicate_nexus_envelopes_are_rejected_fail_closed(self):
        store = LiveStore()
        complete = {
            "event_id": "decision:one",
            "schema_version": 1,
            "snapshot_version": 4,
            "type": "nexus.decision",
            "bot_id": "nexus-trade",
            "epoch": 104,
            "payload": {"id": "decision-one", "decision_id": "decision-one"},
        }

        for missing in ("event_id", "schema_version", "snapshot_version"):
            with self.subTest(missing=missing):
                malformed = dict(complete)
                malformed.pop(missing)
                self.assertFalse(store.apply(malformed))
        self.assertFalse(store.apply({**complete, "schema_version": True}))
        self.assertFalse(store.apply({**complete, "snapshot_version": 0}))
        self.assertEqual(store.snapshot("nexus-trade")["nexus_events"], [])

        self.assertTrue(store.apply(complete))
        same_identity_revision = {
            **complete,
            "event_id": "different-transport-id",
        }
        self.assertFalse(store.apply(same_identity_revision))
        self.assertEqual(len(store.snapshot("nexus-trade")["nexus_events"]), 1)

    def test_sensitive_values_and_local_paths_are_removed_recursively(self):
        store = LiveStore()
        event = {
            "event_id": "report:sanitized",
            "schema_version": 1,
            "snapshot_version": 5,
            "type": "nexus.report",
            "bot_id": "nexus-trade",
            "epoch": 105,
            "payload": {
                "id": "report-safe",
                "summary": "safe summary",
                "unsafe_message": "Bearer secret-value",
                "nested": {
                    "items": [
                        "safe-value",
                        "C:\\Users\\operator\\private-report.json",
                        "file:///app/storage/private-report.json",
                        "https://example.test/export?ticket=secret-value",
                        "super-secret-token",
                        "stored at C:\\Users\\operator\\nested-private.json",
                    ],
                },
            },
        }

        self.assertTrue(store.apply(event))

        serialized = json.dumps(store.snapshot("nexus-trade"))
        self.assertIn("safe summary", serialized)
        self.assertIn("safe-value", serialized)
        self.assertNotIn("secret-value", serialized)
        self.assertNotIn("C:\\\\Users", serialized)
        self.assertNotIn("file:///app", serialized)
        self.assertNotIn("ticket=", serialized)
        self.assertNotIn("super-secret-token", serialized)
        self.assertNotIn("nested-private.json", serialized)

    def test_all_nexus_events_are_idempotent_versioned_and_sanitized(self):
        store = LiveStore()
        event_types = (
            "nexus.runtime",
            "nexus.decision",
            "nexus.trade",
            "nexus.campaign",
            "nexus.report",
            "nexus.trial_changed",
            "nexus.proposal",
            "nexus.version_changed",
            "nexus.learning",
        )

        for revision, event_type in enumerate(event_types, start=1):
            event = {
                "event_id": f"nexus-event-{revision}",
                "schema_version": 1,
                "snapshot_version": revision,
                "type": event_type,
                "bot_id": "nexus-trade",
                "epoch": 100 + revision,
                "payload": {
                    "id": f"payload-{revision}",
                    "lane": Lane.TRIAL.value,
                    "token": "must-not-leak",
                    "real_ticket": "must-not-leak-either",
                },
            }
            sanitized = store.sanitize_event(event)
            self.assertTrue(store.apply(sanitized))
            self.assertFalse(store.apply(sanitized))

        snapshot = store.snapshot("nexus-trade")
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["snapshot_version"], len(event_types))
        self.assertEqual(len(snapshot["nexus_events"]), len(event_types))
        self.assertEqual(
            {event["type"] for event in snapshot["nexus_events"]},
            set(event_types),
        )
        serialized = json.dumps(snapshot).lower()
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("real_ticket", serialized)
        self.assertNotIn('"token"', serialized)

        stale = {
            "event_id": "stale-runtime",
            "schema_version": 1,
            "snapshot_version": 1,
            "type": "nexus.runtime",
            "bot_id": "nexus-trade",
            "payload": {"runtime": {"champion_enabled": 1}},
        }
        self.assertFalse(store.apply(stale))
        self.assertEqual(store.snapshot("nexus-trade")["snapshot_version"], len(event_types))

    def test_nexus_events_are_critical_control_plane_events(self):
        for event_type in (
            "nexus.runtime",
            "nexus.decision",
            "nexus.trade",
            "nexus.position",
            "nexus.campaign",
            "nexus.report",
            "nexus.trial_changed",
            "nexus.proposal",
            "nexus.version_changed",
            "nexus.learning",
        ):
            with self.subTest(event_type=event_type):
                self.assertTrue(is_critical_event({"type": event_type}))

    def test_position_events_are_strict_monotonic_and_closed_is_terminal(self):
        store = LiveStore()

        def position(event_id, status, update_epoch, **payload):
            return {
                "event_id": event_id,
                "schema_version": 1,
                "snapshot_version": 3,
                "type": "nexus.position",
                "bot_id": "nexus-trade",
                "epoch": update_epoch,
                "payload": {
                    "lane": Lane.CHAMPION.value,
                    "contract_id": 7001,
                    "owner_decision_id": "decision-position",
                    "status": status,
                    "update_epoch": update_epoch,
                    **payload,
                },
            }

        self.assertTrue(store.apply(position(
            "position-open", "OPEN", 100,
            stake=0.35, buy_price=0.35, current_spot=123.45, profit=0.0,
        )))
        self.assertFalse(store.apply(position(
            "position-stale", "UPDATED", 99, current_spot=123.40, profit=-0.1,
        )))
        self.assertTrue(store.apply(position(
            "position-update", "UPDATED", 101, current_spot=123.55, profit=0.12,
        )))
        self.assertTrue(store.apply(position(
            "position-closed", "CLOSED", 102, result="won", profit=0.31,
        )))
        self.assertFalse(store.apply(position(
            "position-after-close", "UPDATED", 103, current_spot=123.60,
        )))
        self.assertEqual(store.snapshot("nexus-trade")["positions"], [])

        malformed = position("position-malformed", "OPEN", 110)
        for field, value in (
            ("lane", "unknown"),
            ("contract_id", True),
            ("contract_id", 0),
            ("status", "RECONCILING"),
            ("update_epoch", -1),
        ):
            with self.subTest(field=field, value=value):
                candidate = json.loads(json.dumps(malformed))
                candidate["payload"][field] = value
                candidate["event_id"] = f"bad-{field}-{value}"
                self.assertFalse(store.apply(candidate))


if __name__ == "__main__":
    unittest.main()
