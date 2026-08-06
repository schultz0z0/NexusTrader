import unittest

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
        with self.assertRaises(RealTradingDisabled):
            ensure_demo_account({"account_type": "real"})

        connection = FakeConnection()
        executor = OrderExecutor(connection, account_type="real")
        with self.assertRaises(RealTradingDisabled):
            await executor.buy("proposal-id", 1.0)
        self.assertEqual(connection.sent, [])

    async def test_demo_account_can_execute(self):
        connection = FakeConnection()
        executor = OrderExecutor(connection, account_type="demo")

        result = await executor.buy("proposal-id", 1.0)

        self.assertEqual(result["contract_id"], 42)
        self.assertEqual(connection.sent, [{"buy": "proposal-id", "price": 1.0}])


class ContractSettlementTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
