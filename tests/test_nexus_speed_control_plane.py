import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.app import create_app
from api.live_store import LiveStore
from api.routes.bots import BotPayload
from config.settings import settings
from data.market_data import MarketDataHandler
from database.repository import DatabaseRepository


class NexusSpeedPayloadTests(unittest.TestCase):
    def test_api_normalizes_nexus_speed_to_the_approved_fixed_profile(self):
        payload = BotPayload(
            name="Nexus Speed",
            strategy_id="nexus_speed",
            symbol="R_100",
            duration=5,
            duration_unit="t",
        )

        self.assertEqual(payload.timeframe_seconds, 60)
        self.assertEqual(payload.duration, 5)
        self.assertEqual(payload.duration_unit, "t")
        self.assertEqual(payload.strategy_config, {
            "ema_period": 5,
            "adx_period": 10,
            "adx_threshold": 30,
            "atr_period": 14,
            "min_distance_atr": 0.30,
            "touch_tolerance_bps": 0,
            "ema_flat_tolerance_pips": 1,
            "min_profit_ratio": 0.87,
            "max_entry_delay_ticks": 1,
            "min_closed_candles": 270,
            "touch_window_start_second": 1,
            "touch_window_end_second": 30,
            "blocked_m5_candle_positions": [1, 5],
        })

    def test_api_rejects_nexus_duration_other_than_five_ticks(self):
        for override in ({"duration": 10}, {"duration_unit": "s"}):
            with self.subTest(override=override), self.assertRaises(ValidationError):
                BotPayload(
                    name="Nexus Speed",
                    strategy_id="nexus_speed",
                    duration=override.get("duration", 5),
                    duration_unit=override.get("duration_unit", "t"),
                )

    def test_api_rejects_nexus_profile_drift(self):
        with self.assertRaises(ValidationError):
            BotPayload(
                name="Nexus Speed",
                strategy_id="nexus_speed",
                duration=5,
                duration_unit="t",
                strategy_config={"min_profit_ratio": 0.86},
            )

    def test_api_normalizes_an_approved_adx_threshold(self):
        payload = BotPayload(
            name="Nexus Speed",
            strategy_id="nexus_speed",
            duration=5,
            duration_unit="t",
            strategy_config={"adx_threshold": 25},
        )

        self.assertEqual(payload.strategy_config["adx_threshold"], 25)
        self.assertEqual(payload.strategy_config["min_profit_ratio"], 0.87)

    def test_api_normalizes_legacy_full_profile_without_adx_threshold(self):
        payload = BotPayload(
            name="Legacy Nexus Speed",
            strategy_id="nexus_speed",
            duration=5,
            duration_unit="t",
            strategy_config={
                "ema_period": 5,
                "adx_period": 10,
                "atr_period": 14,
                "min_distance_atr": 0.30,
                "touch_tolerance_bps": 1,
                "ema_flat_tolerance_pips": 1,
                "min_profit_ratio": 0.87,
                "max_entry_delay_ticks": 1,
                "min_closed_candles": 270,
            },
        )

        self.assertEqual(payload.strategy_config["adx_threshold"], 30)
        self.assertEqual(payload.strategy_config["touch_tolerance_bps"], 0)
        self.assertEqual(payload.strategy_config["touch_window_start_second"], 1)
        self.assertEqual(payload.strategy_config["touch_window_end_second"], 30)
        self.assertEqual(payload.strategy_config["blocked_m5_candle_positions"], [1, 5])
        self.assertIs(type(payload.strategy_config["adx_threshold"]), int)

    def test_api_rejects_unapproved_adx_threshold(self):
        with self.assertRaises(ValidationError):
            BotPayload(
                name="Nexus Speed",
                strategy_id="nexus_speed",
                duration=5,
                duration_unit="t",
                strategy_config={"adx_threshold": 21},
            )


class NexusSpeedPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_repository_round_trips_nexus_speed_profile(self):
        payload = BotPayload(
            name="Nexus Speed",
            strategy_id="nexus_speed",
            account_id="VRTC100",
            account_type="demo",
            symbol="R_100",
            duration=5,
            duration_unit="t",
        )
        with tempfile.TemporaryDirectory() as tempdir:
            repository = DatabaseRepository(str(Path(tempdir) / "nexus.db"))
            await repository.init_db()

            created = await repository.create_bot(payload.model_dump())
            stored = await repository.get_bot(created["id"])

        self.assertEqual(stored["strategy_id"], "nexus_speed")
        self.assertEqual(stored["strategy_config"], payload.strategy_config)
        self.assertEqual(stored["duration"], 5)
        self.assertEqual(stored["duration_unit"], "t")
        self.assertEqual(stored["account_type"], "demo")


class NexusSpeedApiResponseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repository = DatabaseRepository(str(Path(self.tempdir.name) / "nexus.db"))
        asyncio.run(self.repository.init_db())
        payload = BotPayload(
            name="Legacy Nexus Speed",
            strategy_id="nexus_speed",
            account_id="VRTC100",
            account_type="demo",
            symbol="R_10",
            duration=5,
            duration_unit="t",
        ).model_dump()
        payload["strategy_config"]["touch_tolerance_bps"] = 1
        payload["strategy_config"].pop("touch_window_start_second")
        payload["strategy_config"].pop("touch_window_end_second")
        payload["strategy_config"].pop("blocked_m5_candle_positions")
        self.stored = asyncio.run(self.repository.create_bot(payload))
        headers = {
            "X-API-Key": settings.DASHBOARD_API_KEY
        } if settings.DASHBOARD_API_KEY else {}
        self.client_context = TestClient(
            create_app(self.repository, LiveStore()),
            headers=headers,
        )
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_list_and_get_normalize_legacy_touch_without_rewriting_storage(self):
        listed = self.client.get("/api/v1/bots")
        fetched = self.client.get(f"/api/v1/bots/{self.stored['id']}")

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(fetched.status_code, 200)
        listed_bot = next(
            bot for bot in listed.json()["data"] if bot["id"] == self.stored["id"]
        )
        self.assertEqual(
            listed_bot["strategy_config"]["touch_tolerance_bps"],
            0,
        )
        self.assertEqual(
            fetched.json()["data"]["strategy_config"]["touch_tolerance_bps"],
            0,
        )
        self.assertEqual(
            listed_bot["strategy_config"]["touch_window_end_second"],
            30,
        )
        self.assertEqual(
            fetched.json()["data"]["strategy_config"]["blocked_m5_candle_positions"],
            [1, 5],
        )
        persisted = asyncio.run(self.repository.get_bot(self.stored["id"]))
        self.assertEqual(persisted["strategy_config"]["touch_tolerance_bps"], 1)
        self.assertNotIn("touch_window_end_second", persisted["strategy_config"])


class NexusSpeedMarketTelemetryTests(unittest.TestCase):
    def test_ema_history_uses_period_five(self):
        points = [
            {
                "time": index * 60,
                "open": float(index),
                "high": float(index),
                "low": float(index),
                "close": float(index),
            }
            for index in range(1, 7)
        ]
        handler = MarketDataHandler(
            object(), indicator_mode="ema", ema_period=5
        )

        ema = handler._ema_history(points)

        self.assertEqual(ema, [
            {"time": 300, "value": 3.0},
            {"time": 360, "value": 4.0},
        ])

    def test_live_store_freezes_ema_reference_during_same_candle(self):
        store = LiveStore()
        store.apply({
            "event_id": "history",
            "type": "market.history",
            "bot_id": "bot-a",
            "epoch": 300,
            "symbol": "R_100",
            "timeframe_seconds": 60,
            "mode": "candles",
            "indicator_mode": "ema",
            "points": [],
            "ema": [{"time": 300, "value": 100.0}],
        })
        store.apply({
            "event_id": "tick-a",
            "type": "market.tick",
            "bot_id": "bot-a",
            "epoch": 361,
            "symbol": "R_100",
            "timeframe_seconds": 60,
            "price": 101.0,
            "indicator_mode": "ema",
            "candle": {"time": 360, "open": 101, "high": 101, "low": 101, "close": 101},
            "ema": 100.5,
        })
        store.apply({
            "event_id": "tick-b",
            "type": "market.tick",
            "bot_id": "bot-a",
            "epoch": 362,
            "symbol": "R_100",
            "timeframe_seconds": 60,
            "price": 102.0,
            "indicator_mode": "ema",
            "candle": {"time": 360, "open": 101, "high": 102, "low": 101, "close": 102},
            "ema": 101.0,
        })

        market = store.snapshot("bot-a")["market"]
        self.assertEqual(market["indicator_mode"], "ema")
        self.assertEqual(market["ema"], [
            {"time": 300, "value": 100.0},
            {"time": 360, "value": 100.5},
        ])
