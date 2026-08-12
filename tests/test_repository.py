import tempfile
import unittest
from pathlib import Path

import aiosqlite

from database.repository import DatabaseRepository


class RepositoryContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "nexus-test.db")
        self.repo = DatabaseRepository(self.db_path)
        await self.repo.init_db()

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def bot_payload(name):
        return {
            "name": name,
            "strategy_id": "bollinger",
            "strategy_config": {"period": 20, "std_dev": 2.0},
            "account_id": "DOT-DEMO",
            "account_type": "demo",
            "symbol": "R_100",
            "timeframe_seconds": 60,
            "duration": 5,
            "duration_unit": "t",
            "initial_stake": 1.0,
            "money_management": "fixed",
            "money_config": {},
            "risk_config": {"stop_loss_daily": 50.0},
        }

    async def test_two_bots_keep_independent_desired_state(self):
        first = await self.repo.create_bot(self.bot_payload("Bollinger A"))
        second = await self.repo.create_bot(self.bot_payload("Bollinger B"))

        await self.repo.set_desired_state(first["id"], "RUNNING")

        first_stored = await self.repo.get_bot(first["id"])
        second_stored = await self.repo.get_bot(second["id"])
        self.assertEqual(first_stored["desired_state"], "RUNNING")
        self.assertEqual(second_stored["desired_state"], "STOPPED")

    async def test_bot_json_configuration_round_trips_as_dictionaries(self):
        created = await self.repo.create_bot(self.bot_payload("Bollinger JSON"))
        stored = await self.repo.get_bot(created["id"])

        self.assertEqual(stored["strategy_config"], {"period": 20, "std_dev": 2.0})
        self.assertEqual(stored["money_config"], {})
        self.assertEqual(stored["risk_config"], {"stop_loss_daily": 50.0})

    async def test_trade_contract_id_is_idempotent_per_bot(self):
        created = await self.repo.create_bot(self.bot_payload("Bollinger Trades"))
        trade = {
            "bot_id": created["id"],
            "session_id": None,
            "strategy_name": "BollingerBands(20, 2.0)",
            "symbol": "R_100",
            "contract_type": "CALL",
            "contract_id": 42,
            "stake": 1.0,
            "payout": 0.0,
            "profit": 0.0,
            "result": "open",
            "status": "open",
            "entry_spot": 100.0,
            "exit_spot": None,
        }
        await self.repo.upsert_trade(trade)
        await self.repo.upsert_trade({**trade, "profit": 0.95, "result": "won", "status": "closed", "exit_spot": 101.0})

        trades = await self.repo.list_trades(bot_id=created["id"])
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["profit"], 0.95)
        self.assertEqual(trades[0]["exit_spot"], 101.0)

    async def test_bot_daily_stats_exclude_old_and_open_trades(self):
        created = await self.repo.create_bot(self.bot_payload("Daily stats"))
        base = {
            "bot_id": created["id"], "session_id": None, "strategy_name": "Donchian+ZigZag",
            "symbol": "R_75", "contract_type": "CALL", "stake": 1.0,
            "payout": 0.0, "result": "won", "entry_spot": 100.0,
        }
        await self.repo.upsert_trade({**base, "contract_id": 101, "profit": 0.8, "status": "closed"})
        await self.repo.upsert_trade({**base, "contract_id": 102, "profit": 99.0, "status": "closed"})
        await self.repo.upsert_trade({**base, "contract_id": 103, "profit": 50.0, "status": "open"})
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE trades SET created_at = '2000-01-01 00:00:00' WHERE contract_id = 102")
            await db.commit()

        pnl, count = await self.repo.get_bot_daily_stats(created["id"])

        self.assertAlmostEqual(pnl, 0.8)
        self.assertEqual(count, 1)

    async def test_nexus_champion_daily_risk_excludes_trial_and_unmanaged_rows(self):
        base = {
            "bot_id": "nexus-trade", "session_id": None,
            "strategy_name": "nexus_trade", "symbol": "R_100",
            "contract_type": "CALL", "stake": 0.5, "payout": 0.0,
            "result": "lost", "status": "closed", "expiry_time": 1234,
            "nexus_version_id": None,
            "campaign_id": None,
        }
        await self.repo.upsert_trade({
            **base, "contract_id": 201, "profit": -0.5,
            "lane": "champion_baseline",
        })
        await self.repo.upsert_trade({
            **base, "contract_id": 202, "profit": 99.0,
            "lane": "challenger_trial",
        })
        await self.repo.upsert_trade({
            **base, "contract_id": 203, "profit": 88.0,
            "lane": "champion_baseline",
        })
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE trades SET risk_applied = 1 WHERE contract_id IN (201, 202)",
            )
            await db.commit()

        result = await self.repo.get_nexus_champion_daily_risk()

        self.assertEqual(result, {
            "profit": -0.5,
            "trades": 1,
            "last_settled_epoch": 1234,
        })

    async def test_session_can_be_marked_closed(self):
        await self.repo.create_session("session-a")

        await self.repo.close_session("session-a", status="stopped")

        async with aiosqlite.connect(self.db_path) as db:
            row = await (await db.execute("SELECT status, end_time FROM sessions WHERE id = ?", ("session-a",))).fetchone()
        self.assertEqual(row[0], "stopped")
        self.assertIsNotNone(row[1])


if __name__ == "__main__":
    unittest.main()
