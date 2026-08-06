import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from api.routes.bots import BotPayload
from database.repository import DatabaseRepository
from strategies.donchian_zigzag import DonchianZigZagStrategy
from utils.indicators import calculate_zigzag


class DonchianProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_database_uses_the_only_supported_strategy(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repository = DatabaseRepository(str(Path(tempdir) / "fresh.db"))
            await repository.init_db()

            bot = await repository.get_default_bot()

        self.assertEqual(bot["name"], "Donchian")
        self.assertEqual(bot["strategy_id"], "donchian")
        self.assertEqual(bot["symbol"], "R_75")
        self.assertEqual(bot["timeframe_seconds"], 60)
        self.assertEqual(bot["duration"], 2)
        self.assertEqual(bot["duration_unit"], "m")

    async def test_default_payload_uses_donchian_profile(self):
        payload = BotPayload(name="Donchian local")

        self.assertEqual(payload.strategy_id, "donchian")
        self.assertEqual(payload.symbol, "R_75")
        self.assertEqual(payload.timeframe_seconds, 60)
        self.assertEqual(payload.duration, 2)
        self.assertEqual(payload.duration_unit, "m")

    async def test_removed_strategy_is_rejected_at_the_api_boundary(self):
        with self.assertRaises(ValidationError):
            BotPayload(name="Legado", strategy_id="bollinger")

    async def test_fixed_timeframe_and_expiration_cannot_drift_at_api_boundary(self):
        for override in (
            {"timeframe_seconds": 300},
            {"duration": 3},
            {"duration_unit": "t"},
        ):
            with self.subTest(override=override), self.assertRaises(ValidationError):
                BotPayload(name="Perfil divergente", **override)

    async def test_strategy_runtime_parameters_remain_unchanged(self):
        strategy = DonchianZigZagStrategy()

        self.assertEqual(strategy.period, 21)
        self.assertEqual(strategy.zigzag_dev, 0.0)
        self.assertEqual(strategy.duration, 2)
        self.assertEqual(strategy.duration_unit, "m")

    async def test_production_zigzag_preserves_alternating_pivots(self):
        candles = [
            {"time": index * 60, "high": high, "low": low, "close": (high + low) / 2}
            for index, (high, low) in enumerate(
                [
                    (100, 99),
                    (101, 100),
                    (102, 101),
                    (101, 98),
                    (100, 97),
                    (103, 99),
                    (104, 100),
                    (101, 96),
                ]
            )
        ]

        pivots = calculate_zigzag(candles, depth=3, deviation=0.0, backstep=1)

        self.assertGreaterEqual(len(pivots), 2)
        self.assertTrue(all(left["type"] != right["type"] for left, right in zip(pivots, pivots[1:])))
        self.assertTrue(all(point["time"] in {candle["time"] for candle in candles} for point in pivots))

    async def test_production_zigzag_requires_its_configured_depth(self):
        candles = [
            {"time": index * 60, "high": 100 + index, "low": 99 + index, "close": 99.5 + index}
            for index in range(14)
        ]

        self.assertEqual(calculate_zigzag(candles, depth=15, deviation=0.0, backstep=3), [])


if __name__ == "__main__":
    unittest.main()
