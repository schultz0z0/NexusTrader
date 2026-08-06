import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
