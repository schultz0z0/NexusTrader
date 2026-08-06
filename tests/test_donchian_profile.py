import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from api.routes.bots import BotPayload
from database.repository import DatabaseRepository
from strategies.donchian_zigzag import DonchianZigZagStrategy


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

    async def test_strategy_runtime_parameters_remain_unchanged(self):
        strategy = DonchianZigZagStrategy()

        self.assertEqual(strategy.period, 21)
        self.assertEqual(strategy.zigzag_dev, 0.0)
        self.assertEqual(strategy.duration, 2)
        self.assertEqual(strategy.duration_unit, "m")


if __name__ == "__main__":
    unittest.main()
