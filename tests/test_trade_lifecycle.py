import unittest
from unittest.mock import AsyncMock, patch

from core.bot_session import BotSession
from trading.executor import OrderExecutor
from trading.monitor import ContractMonitor
from trading.safety import RealTradingDisabled, ensure_demo_account


class FakeConnection:
    def __init__(self):
        self.sent = []
        self.subscriptions = {}
        self.unsubscribed = []

    async def send(self, request):
        self.sent.append(request)
        return {"buy": {"contract_id": 42}}

    async def subscribe(self, key, request, handler):
        self.subscriptions[key] = handler
        return "sub-42"

    async def unsubscribe(self, key):
        self.unsubscribed.append(key)


class DemoExecutionGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_account_cannot_execute(self):
        from config.settings import settings
        previous = settings.ALLOW_REAL_TRADING
        settings.ALLOW_REAL_TRADING = False
        try:
            with self.assertRaises(RealTradingDisabled):
                ensure_demo_account({"account_type": "real"})

            connection = FakeConnection()
            executor = OrderExecutor(connection, account_type="real")
            with self.assertRaises(RealTradingDisabled):
                await executor.buy("proposal-id", 1.0)
            self.assertEqual(connection.sent, [])
        finally:
            settings.ALLOW_REAL_TRADING = previous

    async def test_demo_account_can_execute(self):
        connection = FakeConnection()
        executor = OrderExecutor(connection, account_type="demo")

        result = await executor.buy("proposal-id", 1.0)

        self.assertEqual(result["contract_id"], 42)
        self.assertEqual(connection.sent, [{"buy": "proposal-id", "price": 1.0}])


class ContractSettlementTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_settlement_callback_is_retried_on_next_sold_update(self):
        connection = FakeConnection()
        monitor = ContractMonitor(connection)
        attempts = []

        async def on_settled(contract):
            attempts.append(contract["contract_id"])
            if len(attempts) == 1:
                raise RuntimeError("database temporarily unavailable")

        await monitor.monitor_contract(42, on_settled)
        callback = connection.subscriptions["contract:42"]
        sold = {"proposal_open_contract": {"contract_id": 42, "is_sold": 1, "status": "won", "profit": "0.95"}}

        with self.assertRaises(RuntimeError):
            await callback(sold)
        await callback(sold)

        self.assertEqual(attempts, [42, 42])
        self.assertEqual(connection.unsubscribed, ["contract:42"])

    async def test_duplicate_sold_update_settles_contract_once(self):
        connection = FakeConnection()
        monitor = ContractMonitor(connection)
        settlements = []

        async def on_settled(contract):
            settlements.append(contract["contract_id"])

        await monitor.monitor_contract(42, on_settled)
        callback = connection.subscriptions["contract:42"]
        sold = {
            "proposal_open_contract": {
                "contract_id": 42,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 1,
                "status": "won",
                "profit": "0.95",
                "payout": "1.95",
            }
        }
        await callback(sold)
        await callback(sold)

        self.assertEqual(settlements, [42])
        self.assertEqual(connection.unsubscribed, ["contract:42"])

    async def test_open_contract_update_is_forwarded_for_live_chart(self):
        connection = FakeConnection()
        monitor = ContractMonitor(connection)
        updates = []

        async def on_settled(contract):
            return None

        async def on_update(contract):
            updates.append(contract["current_spot"])

        await monitor.monitor_contract(42, on_settled, on_update_callback=on_update)
        callback = connection.subscriptions["contract:42"]
        await callback({
            "proposal_open_contract": {
                "contract_id": 42,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 0,
                "current_spot": "100.25",
                "profit": "0.10",
            }
        })

        self.assertEqual(updates, ["100.25"])


class CrashRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_wait_has_a_bounded_failure_state(self):
        class Repository:
            async def touch_bot_heartbeat(self, bot_id):
                return None

        class Publisher:
            async def publish(self, event):
                return True

        session = BotSession(Repository(), {"id": "bot-a"}, publisher=Publisher())
        session._active_contracts.add(42)

        with self.assertRaises(TimeoutError):
            await session._wait_for_active_contracts(timeout_seconds=0.01)

    async def test_db_owned_contract_is_resubscribed_even_when_portfolio_is_empty(self):
        class Repository:
            def __init__(self):
                self.upserts = []

            async def list_trades(self, bot_id, limit=1000):
                return [{
                    "bot_id": bot_id, "contract_id": 42, "contract_type": "CALL",
                    "symbol": "R_75", "stake": 1.0, "status": "open",
                }]

            async def upsert_trade(self, trade):
                self.upserts.append(trade)

        class Publisher:
            async def publish(self, event):
                return True

        class Monitor:
            def __init__(self):
                self.contracts = []

            async def monitor_contract(self, contract_id, on_settled, on_update_callback=None):
                self.contracts.append(contract_id)

        class Strategy:
            def name(self):
                return "Donchian+ZigZag"

        repository = Repository()
        session = BotSession(repository, {
            "id": "bot-a", "symbol": "R_75", "initial_stake": 1.0,
        }, publisher=Publisher())
        session._connection = object()
        monitor = Monitor()

        with patch("core.bot_session.CrashRecoveryHandler") as recovery_type:
            recovery_type.return_value.check_open_contracts = AsyncMock(return_value=[])
            await session._recover_owned_contracts(monitor, Strategy(), object())

        self.assertEqual(monitor.contracts, [42])
        self.assertEqual(session._active_contracts, {42})
        self.assertEqual(repository.upserts, [])


if __name__ == "__main__":
    unittest.main()
