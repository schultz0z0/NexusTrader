import tempfile
import unittest
from pathlib import Path

import aiosqlite

from database.repository import DatabaseRepository
from nexus_trade.constants import NEXUS_TRADE_BOT_ID
from nexus_trade.domain import CampaignStatus, Lane, VersionStatus
from nexus_trade.repository import NexusTradeRepository


class NexusTradeRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "nexus-trade.db")
        self.repo = DatabaseRepository(self.db_path)
        self.nexus = NexusTradeRepository(self.db_path)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_init_provisions_exactly_one_nexus_trade(self):
        await self.repo.init_db()
        await self.repo.init_db()

        bots = [bot for bot in await self.repo.list_bots() if bot["strategy_id"] == "nexus_trade"]

        self.assertEqual([bot["id"] for bot in bots], [NEXUS_TRADE_BOT_ID])
        self.assertEqual(bots[0]["symbol"], "R_100")
        self.assertEqual(bots[0]["timeframe_seconds"], 60)
        self.assertEqual(bots[0]["duration"], 58)
        self.assertEqual(bots[0]["duration_unit"], "s")

    async def test_snapshot_has_versioned_champion_and_active_trial_campaign(self):
        await self.repo.init_db()

        snapshot = await self.nexus.get_runtime_snapshot()

        self.assertEqual(snapshot["bot"]["id"], "nexus-trade")
        self.assertEqual(
            {lane["lane"] for lane in snapshot["lanes"]},
            {Lane.CHAMPION.value, Lane.TRIAL.value},
        )
        champion = next(lane for lane in snapshot["lanes"] if lane["lane"] == Lane.CHAMPION.value)
        self.assertEqual(champion["version"]["status"], VersionStatus.CHAMPION.value)
        self.assertEqual(champion["version"]["name"], "Champion V1")
        self.assertEqual(champion["version"]["snapshot"]["bollinger"], {"period": 20, "std_dev": 2, "ma": "SMA"})
        self.assertEqual(champion["version"]["snapshot"]["adx"], {"period": 14, "max_entry": 22})
        self.assertEqual(len(snapshot["active_campaigns"]), 1)
        self.assertEqual(snapshot["active_campaigns"][0]["lane"], Lane.TRIAL.value)
        self.assertEqual(snapshot["active_campaigns"][0]["status"], CampaignStatus.ACTIVE.value)

    async def test_schema_rejects_a_second_nexus_trade_singleton(self):
        await self.repo.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            with self.assertRaises(aiosqlite.IntegrityError):
                await db.execute(
                    """
                    INSERT INTO bot_instances (
                        id, name, strategy_id, strategy_config, account_id, account_type,
                        symbol, timeframe_seconds, duration, duration_unit, initial_stake,
                        money_management, money_config, risk_config
                    ) VALUES (?, ?, 'nexus_trade', '{}', '', 'demo', 'R_100', 60, 58, 's', 0.35, 'fixed', '{}', '{}')
                    """,
                    ("another-nexus-trade", "Duplicate NexusTrade"),
                )

    async def test_nexus_migrations_add_lane_identity_to_trade_journals(self):
        await self.repo.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            trade_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(trades)")).fetchall()}
            intent_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(order_intents)")).fetchall()}

        expected = {"lane", "nexus_version_id", "campaign_id", "decision_id", "entry_delay_ms"}
        self.assertTrue(expected.issubset(trade_columns))
        self.assertTrue(expected.issubset(intent_columns))


if __name__ == "__main__":
    unittest.main()
