import tempfile
import unittest
from pathlib import Path

from database.repository import DatabaseRepository
from risk.circuit_breaker import CircuitBreaker
from strategies.base import MoneyManager


class PersistentRiskStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repository = DatabaseRepository(str(Path(self.tempdir.name) / "risk.db"))
        await self.repository.init_db()
        self.bot = await self.repository.create_bot({
            "name": "Risk state",
            "strategy_id": "donchian",
            "account_id": "DOT100",
            "account_type": "demo",
            "symbol": "R_75",
            "initial_stake": 1.0,
            "money_management": "martingale",
            "money_config": {"multiplier": 2.0, "max_levels": 3},
            "risk_config": {"max_consecutive_losses": 3, "cooldown_minutes": 15},
        })

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    def trade(self, contract_id, profit=-1.0, result="lost"):
        return {
            "bot_id": self.bot["id"], "session_id": "session-a",
            "strategy_name": "DonchianZigZag(21, 1.0)", "symbol": "R_75",
            "contract_type": "CALL", "contract_id": contract_id,
            "stake": 1.0, "payout": max(0.0, 1.0 + profit),
            "profit": profit, "result": result, "status": "closed",
        }

    async def settle(self, trade, epoch=1000):
        return await self.repository.settle_trade_and_risk(
            trade,
            money_management="martingale",
            money_config={"multiplier": 2.0, "max_levels": 3},
            risk_config={"max_consecutive_losses": 3, "cooldown_minutes": 15},
            initial_stake=1.0,
            settled_epoch=epoch,
        )

    async def test_trade_close_and_risk_advance_are_idempotent(self):
        first = await self.settle(self.trade(42))
        duplicate = await self.settle(self.trade(42))

        self.assertTrue(first["applied"])
        self.assertFalse(duplicate["applied"])
        self.assertEqual(duplicate["state"]["current_stake"], 2.0)
        self.assertEqual(duplicate["state"]["current_level"], 1)
        self.assertEqual(duplicate["state"]["circuit_consecutive_losses"], 1)

    async def test_money_and_circuit_runtime_restore_exact_persisted_snapshot(self):
        result = await self.settle(self.trade(42))
        money = MoneyManager(mode="martingale", initial_stake=1.0)
        circuit = CircuitBreaker({"max_consecutive_losses": 3, "cooldown_minutes": 15})

        money.restore_state(result["state"])
        circuit.restore_state(result["state"])

        self.assertEqual(money.get_stake(), 2.0)
        self.assertEqual(money.current_level, 1)
        self.assertEqual(circuit.consecutive_losses, 1)

    async def test_circuit_breaker_trip_survives_restart(self):
        await self.settle(self.trade(41), epoch=1000)
        await self.settle(self.trade(42), epoch=1001)
        result = await self.settle(self.trade(43), epoch=1002)

        self.assertEqual(result["state"]["circuit_consecutive_losses"], 3)
        self.assertEqual(result["state"]["circuit_tripped_at"], 1002.0)

        stored = await self.repository.get_risk_state(self.bot["id"], initial_stake=1.0)
        self.assertEqual(stored, result["state"])

    async def test_upgrade_backfills_legacy_closed_trades_before_next_settlement(self):
        legacy = self.trade(55)
        await self.repository.upsert_trade(legacy)

        await self.repository.init_db()
        stored = await self.repository.get_risk_state(
            self.bot["id"], initial_stake=1.0
        )

        self.assertEqual(stored["current_stake"], 2.0)
        self.assertEqual(stored["current_level"], 1)
        trades = await self.repository.list_trades(self.bot["id"])
        self.assertEqual(trades[0]["risk_applied"], 1)


if __name__ == "__main__":
    unittest.main()
