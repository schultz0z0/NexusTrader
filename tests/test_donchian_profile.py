import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from api.routes.bots import BotPayload
from database.repository import DatabaseRepository
from strategies.donchian_zigzag import DonchianZigZagStrategy
from strategies.base import Signal
from utils.indicators import calculate_zigzag


class DonchianProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_signal_construction_keeps_tick_metadata_optional(self):
        signal = Signal("CALL", "fixture", 100.0, 123)

        self.assertIsNone(signal.tick_sequence)
        self.assertIsNone(signal.candle_time)

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

    async def test_indicator_parameters_cannot_drift_at_api_boundary(self):
        with self.assertRaises(ValidationError):
            BotPayload(
                name="Indicador divergente",
                strategy_config={"period": 20, "deviation": 1, "depth": 15, "backstep": 3},
            )

        payload = BotPayload(name="Perfil fixo")
        self.assertEqual(payload.strategy_config, {
            "period": 21, "deviation": 1, "depth": 15, "backstep": 3,
        })

    async def test_strategy_runtime_parameters_remain_unchanged(self):
        strategy = DonchianZigZagStrategy()

        self.assertEqual(strategy.period, 21)
        self.assertEqual(strategy.zigzag_dev, 1.0)
        self.assertEqual(strategy.zigzag_depth, 15)
        self.assertEqual(strategy.zigzag_backstep, 3)
        self.assertEqual(strategy.duration, 2)
        self.assertEqual(strategy.duration_unit, "m")

    @staticmethod
    def _upper_touch_candles():
        falling = [
            {"time": index * 60, "high": 110 - index * 0.5,
             "low": 109 - index * 0.5, "close": 109.5 - index * 0.5}
            for index in range(20)
        ]
        rising = [
            {"time": index * 60, "high": 100 + (index - 19) * 0.8,
             "low": 99 + (index - 19) * 0.8, "close": 99.5 + (index - 19) * 0.8}
            for index in range(20, 42)
        ]
        return falling + rising

    @staticmethod
    def _lower_touch_candles():
        rising = [
            {"time": index * 60, "high": 100 + index * 0.5,
             "low": 99 + index * 0.5, "close": 99.5 + index * 0.5}
            for index in range(20)
        ]
        falling = [
            {"time": index * 60, "high": 110 - (index - 19) * 0.8,
             "low": 109 - (index - 19) * 0.8, "close": 109.5 - (index - 19) * 0.8}
            for index in range(20, 42)
        ]
        return rising + falling

    async def test_live_high_zigzag_tip_touching_upper_donchian_emits_put(self):
        candles = self._upper_touch_candles()
        strategy = DonchianZigZagStrategy()

        signal = strategy.analyze(
            [{"epoch": candles[-1]["time"] + 20, "quote": candles[-1]["close"]}],
            candles,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.action, "PUT")
        self.assertIn("ZigZag HIGH", signal.reason)

    async def test_live_low_zigzag_tip_touching_lower_donchian_emits_call(self):
        candles = self._lower_touch_candles()
        strategy = DonchianZigZagStrategy()

        signal = strategy.analyze(
            [{"epoch": candles[-1]["time"] + 20, "quote": candles[-1]["close"]}],
            candles,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.action, "CALL")
        self.assertIn("ZigZag LOW", signal.reason)

    async def test_touch_requires_the_current_zigzag_tip_not_only_the_tick_price(self):
        candles = self._upper_touch_candles()
        candles.append({
            "time": candles[-1]["time"] + 60,
            "high": candles[-1]["high"] - 0.5,
            "low": candles[-1]["low"] - 0.5,
            "close": candles[-1]["close"] - 0.5,
        })
        upper = max(item["high"] for item in candles[-21:])

        signal = DonchianZigZagStrategy().analyze(
            [{"epoch": candles[-1]["time"] + 30, "quote": upper}],
            candles,
        )

        self.assertIsNone(signal)

    async def test_same_live_tip_is_emitted_only_once_even_with_new_ticks(self):
        candles = self._upper_touch_candles()
        strategy = DonchianZigZagStrategy()

        first = strategy.analyze(
            [{"epoch": candles[-1]["time"] + 10, "quote": candles[-1]["close"]}],
            candles,
        )
        duplicate = strategy.analyze(
            [{"epoch": candles[-1]["time"] + 40, "quote": candles[-1]["close"]}],
            candles,
        )

        self.assertIsNotNone(first)
        self.assertIsNone(duplicate)

    async def test_zigzag_deviation_is_one_percent_not_zero(self):
        candles = [
            {"time": index * 60, "high": high, "low": low, "close": (high + low) / 2}
            for index, (high, low) in enumerate([
                (100.0, 99.8), (100.2, 99.9), (100.1, 99.7),
                (100.3, 99.8), (100.2, 99.6), (100.4, 99.7),
            ])
        ]

        unfiltered = calculate_zigzag(candles, depth=2, deviation=0.0, backstep=1)
        configured = calculate_zigzag(candles, depth=2, deviation=1.0, backstep=1)

        self.assertGreater(len(unfiltered), len(configured))

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

        pivots = calculate_zigzag(candles, depth=3, deviation=1.0, backstep=1)

        self.assertGreaterEqual(len(pivots), 2)
        self.assertTrue(all(left["type"] != right["type"] for left, right in zip(pivots, pivots[1:])))
        self.assertTrue(all(point["time"] in {candle["time"] for candle in candles} for point in pivots))

    async def test_production_zigzag_requires_its_configured_depth(self):
        candles = [
            {"time": index * 60, "high": 100 + index, "low": 99 + index, "close": 99.5 + index}
            for index in range(14)
        ]

        self.assertEqual(calculate_zigzag(candles, depth=15, deviation=1.0, backstep=3), [])


if __name__ == "__main__":
    unittest.main()
